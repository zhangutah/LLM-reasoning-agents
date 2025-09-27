from constants import CodeSearchAPIName, LanguageType
import os
import requests
from agent_tools.code_tools.parsers.cpp_parser import CPPParser
from agent_tools.code_tools.parsers.c_parser import CParser
import time
from typing import List, Dict, Optional, Any
import subprocess 
import json
from bench_cfg import BenchConfig

def get_jaccard_sim(str1: str, str2: str) -> float: 
    a = set(str1.split()) 
    b = set(str2.split())
    c = a.intersection(b)
    if len(a) == 0 and len(b) == 0 and len(c) == 0:
        return 1
    else:
        return float(len(c)) / (len(a) + len(b) - len(c))
        
class CodeSearch():
    def __init__(self, api_name: CodeSearchAPIName, project_lang: LanguageType):
        self.api_name = api_name
        self.project_lang = project_lang
        if self.api_name not in [CodeSearchAPIName.Github, CodeSearchAPIName.Sourcegraph]:
            raise NotImplementedError(f"API {self.api_name} is not implemented.")
        
        self.parser = self.get_parser()

    def get_parser(self) -> type:
        """
        Returns the appropriate parser based on the project language.
        """
        if self.project_lang == LanguageType.CPP:
            return CPPParser

        elif self.project_lang == LanguageType.C:
            return CParser
        else:
            raise NotImplementedError(f"Parser for {self.project_lang} is not implemented.")
        
    @staticmethod
    # Function to fetch source code as a string
    def _fetch_source_code(html_url: str, timeout: int = 5) -> Optional[str]:
        # Set timeout for the GET request
        try:
            response = requests.get(html_url, timeout=timeout)
        except requests.exceptions.Timeout:
            print("Timeout error for URL:", html_url)
            return None 
        
        if response.status_code == 200:
            return response.text  # Return the content as a string
        else:
            print(f"Failed to fetch: {html_url} (Status Code: {response.status_code})")
            return None

    def extract_caller_code(self, symbol_name: str, source_code: str) -> list[str]:
            # find the function call linenumber with re
        call_lines: list[int] = []
        for i, line in enumerate(source_code.split('\n')):
            if f"{symbol_name}(" in line:
                call_lines.append(i)
        if not call_lines:
            return []

        # check if the function is called by AST
        parser = self.parser(None, source_code)

        code_snippet: list[str] = []
        for lineno in call_lines:
            # find the function call code
            ref_code = parser.get_ref_source(symbol_name, lineno)
            
            # save the caller code
            if ref_code:
                code_snippet.append(ref_code)
        
        return code_snippet
    
    def _search_github(self, symbol_name: str, num_results: int = 200)-> List[Dict[str, str]]:
        """
        Search for a given symbol name on GitHub and retrieve source code snippets where the symbol is called.
        Args:
            symbol_name (str): The name of the symbol to search for.
            num_results (int, optional): The maximum number of search results to retrieve. Defaults to 200.
        Returns:
            List[Dict[str, str]]: A list of dictionaries containing file paths and source code snippets where the symbol is called.
        Raises:
            Exception: If the GitHub API request fails or if the GITHUB_TOKEN environment variable is not set.
        """


        github_token = os.getenv("GITHUB_TOKEN")
        assert github_token, "Please set the GITHUB_TOKEN environment variable."

        base_url = "https://api.github.com/search/code"
        headers = {
            "Authorization": f"Bearer {github_token}",
            "Accept": "application/vnd.github.v3+json"
        }

        # Step 1: Search for the symbol
        params = {
            "q": f"{symbol_name}(",  # Search query
            "per_page": num_results     # Limit the number of results
        }
        
        response = requests.get(base_url, headers=headers, params=params)
        if response.status_code != 200:
            raise Exception(f"Failed to search GitHub API: {response.status_code}, {response.text}")
        
        search_results = response.json()
        
        # Step 2: Extract file paths and fetch source code
        code_results = []
        for item in search_results.get("items", []):
            file_path = item["path"]
            html_url = item["html_url"]

            # filter the file type
            file_type = file_path.split('.')[-1]
            if file_type not in [ 'c', 'cc', 'cpp', 'cxx', 'c++', "java"]:
                continue

            #  to true download url
            raw_url = html_url.replace("github.com", "raw.githubusercontent.com").replace("/blob/", "/")
            # Fetch the source code
            time.sleep(1)
            source_code = self._fetch_source_code(raw_url)


            try:
                # with repo name
                full_path = "{}/{}".format(item["repository"]["full_name"], file_path)
            except:
                full_path = file_path

            if source_code:
                code_results.append({
                    "file_path": full_path,
                    "source_code": source_code
                })

        code_snippet = []
        # screen out if the function is not called since the search is not perfect
        for res in code_results:
            file_path = res["file_path"]
            source_code = res["source_code"]

            # check if the symbol is called in the source code
            called_code = self.extract_caller_code(symbol_name, source_code)
            if called_code:
                code_snippet.extend(called_code)

        return code_snippet


    def _search_sourcegraph(self, symbol_name: str, num_results: int = 0) -> List[str]:
        SRC_ACCESS_TOKEN = os.environ["SRC_ACCESS_TOKEN"]
        if not SRC_ACCESS_TOKEN:
            raise ValueError("Please set the SRC_ACCESS_TOKEN environment variable.")
        file_ext_dict = {
            LanguageType.C: ["c", "cc", "cpp", "cxx"],
            LanguageType.CPP: ["c", "cc", "cpp", "cxx"],
            LanguageType.JAVA: ["java"]
        }

        if self.project_lang not in file_ext_dict.keys():
            raise ValueError(f"Unsupported project language: {self.project_lang}")
        if num_results <= 0:
            # Use 'all' to fetch all results
            num_results = "all" # type: ignore

        file_exts = file_ext_dict[self.project_lang]
        # Construct the file filter string
        # file:\\.c$|\\.cpp$
        file_filter = '|'.join([f"\\.{ext}$" for ext in file_exts])
        
        search_res = None
        for _ in range(3):
            try:
                print('do query for api %s' % (symbol_name))
                output = subprocess.check_output(f'src search -json "file:{file_filter} lang:{self.project_lang.value.lower()} count:{num_results} {symbol_name}"',shell=True, env=dict(os.environ, SRC_ACCESS_TOKEN=SRC_ACCESS_TOKEN), timeout=60)
                search_res = json.loads(output.decode("utf-8"))
                if search_res:
                    break

            except Exception as e:
                print('meet exception when crawling %s' % (e))
                print('sleep 20s and try again')
                time.sleep(20)

        # extract the code snippets from the search results
        code_snippet: List[str] = []
        if not search_res:
            print("No search results found.")
            return []

        #for result in info['Results'][:1000]:
        for result in search_res['Results']:
            result_cnt = result['file']['content']
            code_snippet.append(result_cnt)  

      
        # extract the caller code
        called_code_list: List[str] = []
        for src_code in code_snippet:
            # check if the symbol is called in the source code
            called_code = self.extract_caller_code(symbol_name, src_code)
            if called_code:
                called_code_list.extend(called_code)

        # deduplicate the code snippets
        called_code_list = self.deduplicate(called_code_list)

        return called_code_list

    def deduplicate(self, code_snippet: List[str], threshold: float = 0.9) -> List[str]:
        """
        Deduplicate code snippets based on Jaccard similarity.
        Args:
            code_snippet (List[str]): List of code snippets to deduplicate.
        Returns:
            List[str]: Deduplicated list of code snippets.
        """

        # using the Jaccard similarity to deduplicate code snippets
        unique_snippets: List[str] = []
        for snippet in code_snippet:
            is_unique = True
            for unique_snippet in unique_snippets:
                if get_jaccard_sim(snippet, unique_snippet) > threshold:
                    is_unique = False
                    break
            if is_unique:
                unique_snippets.append(snippet)
        return unique_snippets

    def search(self, query: str, num_results: int = 200):
        if self.api_name == CodeSearchAPIName.Github:
            code_snippet = self._search_github(query, num_results)
        elif self.api_name == CodeSearchAPIName.Sourcegraph:
            code_snippet = self._search_sourcegraph(query, num_results)
        else:
            raise NotImplementedError

        return code_snippet



def search_public_usage(search_api: CodeSearchAPIName, function_name: str, project_name: str, project_lang: LanguageType, benchcfg: BenchConfig) -> list[dict[str, str]]:

    assert search_api in [CodeSearchAPIName.Sourcegraph], f"Unsupported API: {search_api}"
    # ranked cache file
    cached_file = benchcfg.cache_root / project_name / f"{function_name}_references_{search_api.value}.json"
    if cached_file.exists():
        # read the json file
        with open(cached_file, "r") as f:
            code_usages = json.load(f)
        return code_usages
    else:
        # search the code usage from the public code

        code_search = CodeSearch(search_api, project_lang)
        searched_res = code_search.search(function_name, num_results=0)

        # dump the results to json file
        code_usages: list[dict[str, Any]] = [{"source_code": code} for code in searched_res]
        cached_file.parent.mkdir(parents=True, exist_ok=True)
        with open(cached_file, "w") as f:
            json.dump(code_usages, f, indent=4)
    
        return code_usages

if __name__ == "__main__":
    code_search = CodeSearch(CodeSearchAPIName.Sourcegraph, LanguageType.C)
    results = code_search.search("dns_compress_init", num_results=0)
    print(f"Found {len(results)} results.")
    for result in results:
        print("-" * 50)
        print(result)