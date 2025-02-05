from constants import CodeSearchAPIName, LanguageType
import os
import requests
from tools.language_parser import LanguageParser
import re
import time

class CodeSearch():
    def __init__(self, api_name: str, project_lang: str):
        self.api_name = api_name
        self.project_lang = project_lang


    @staticmethod
    # Function to fetch source code as a string
    def _fetch_source_code(html_url: str, timeout: int = 5) -> str:
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

    def _search_github(self, symbol_name: str, num_results: int = 200):
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

            # find the function call linenumber with re
            call_lines = []
            for i, line in enumerate(source_code.split('\n')):
                if f"{symbol_name}(" in line:
                    call_lines.append(i)
            if not call_lines:
                continue

            # check if the function is called by AST
            parser = LanguageParser(None, source_code,  self.project_lang)
            
            for lineno in call_lines:
                # find the function call code
                ref_code = parser.get_ref_source(symbol_name, lineno)
               
                # save the caller code
                if ref_code:
                    code_snippet.append({
                        "file_path": file_path,
                        "source_code": ref_code,
                        "line": lineno
                    })
        # first 
        return code_snippet


    def deduplicate(self, code_snippet):

        pass

    def search(self, query: str, num_results: int = 200):
        if self.api_name == CodeSearchAPIName.Github:
            code_snippet = self._search_github(query, num_results)
        else:
            raise NotImplementedError

        return code_snippet


if __name__ == "__main__":
    code_search = CodeSearch(CodeSearchAPIName.Github, LanguageType.C)
    results = code_search.search("cJSON_ReplaceItemInObjectCaseSensitive", num_results=10)
    print(f"Found {len(results)} results.")
    for result in results:
        print(result["file_path"])
        print(result["source_code"])
        print("-" * 50)