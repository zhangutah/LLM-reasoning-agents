import json
import os
import argparse
import subprocess as sp
import random
from tools.code_tools.parsers.c_cpp_parser import CCPPParser
from tools.code_tools.parsers.java_parser import JavaParser
from constants import LanguageType, LSPFunction, LSPResults
from pathlib import Path
from typing import Any


def extract_name(function_signature: str)-> str:
    # Remove the parameters by splitting at the first '('
    function_name = function_signature.split('(')[0]
    # Split the function signature into tokens to isolate the function name
    tokens = function_name.strip().split()
    assert len(tokens) > 0

    # The function name is the last token, this may include namespaces ::
    function_name = tokens[-1]

    # split the function name by ::
    function_name = function_name.split("::")[-1]

    # remove * from the function name
    if "*" in function_name:
        function_name = function_name.replace("*", "")

    return function_name


class ParserCodeRetriever():
    def __init__(self, workdir: str,  project_lang: LanguageType, symbol_name: str, lsp_function: LSPFunction, max_try: int = 100):
   
        self.project_root = workdir
        self.symbol_name = symbol_name
        self.lsp_function = lsp_function
        self.project_lang = project_lang
        self.max_try = max_try
        self.lang_parser = self.get_language_parser()
    
    def get_language_parser(self):
        if self.project_lang in [LanguageType.C, LanguageType.CPP]:
            return CCPPParser
        elif self.project_lang == LanguageType.JAVA:
            return JavaParser
        else:
            raise Exception(f"Language {self.project_lang} not supported.")
    
    def fetch_code(self, file_path: str, lineno: int, charpos: int) -> list[dict[str, Any]]:
        """
        Find the definition of a symbol in a C or C++ file using Clangd LSP.
        Args:
            file_path (str): The C++ source file including the symbol.
            lineno (int): The line number where the symbol is located.
            charpos (int): The character position within the line where the symbol is located.
        Returns:
            list[dict]: The list of source code and corresponding file.
        Raises:
            Exception: If there is an error during the request to the LSP server.
        """
        # cpp for C++
        ret_list:list[dict[str, Any]] = []
        query_key = ""
        start_line = 0
        parser = self.lang_parser(Path(file_path), source_code=None, project_lang=self.project_lang)
        if self.lsp_function == LSPFunction.References:
            # get the full source code of the symbol
            source_code = parser.get_ref_source(self.symbol_name, lineno) # type: ignore
        else:
            query_key, source_code, start_line = parser.get_symbol_source(self.symbol_name, lineno, self.lsp_function)

        if source_code:
            ret_list.append({"source_code": source_code, "file_path": file_path, "line": lineno, "type": query_key, "start_line": start_line})

        return ret_list

    def get_symbol_info_helper(self) -> tuple[str, list[dict[str, Any]]]:
        """
        Finds information about a given symbol in the project.
        This method searches for the specified symbol within the project directory
        using the `grep` command. It then parse all file contains the symbol and extract information using tree-sitter.
        Returns:
            list[dict]: A list of dictionaries containing information about the symbol.
                If the symbol is not found, an empty string is returned.
        """
        
        # Execute `find` command to recursively list files and directories
        if self.lsp_function == LSPFunction.References:
            cmd = f"grep --binary-files=without-match -rn {self.project_root} -e  '{self.symbol_name}('"
        else:
            cmd = f"grep --binary-files=without-match -rnw {self.project_root} -e  {self.symbol_name}"

        results = sp.run(cmd, shell=True, stdout=sp.PIPE, stderr=sp.STDOUT,  text=True)
        output = results.stdout.strip()

        if not output:
            return LSPResults.NoResult.value, []

        # the location may be in the comments or in the string literals
        # find the file path, line number and character position
        all_lines = output.splitlines()

        # filter some files by file type
        filtered_lines: list[tuple[str, int, int]] = []
        for line in all_lines:
            
            parts = line.split(':', 2)
            # check if the line is valid
            if len(parts) < 3:
                continue

            file_path, lineno, content = parts
            # filter the other files (.md, .txt, etc)
            file_type = file_path.split('.')[-1]

            if self.lsp_function in [LSPFunction.Declaration, LSPFunction.StructFunctions]:
                filter_header = [ 'h', 'hpp', 'hh', 'hxx', "java"]
            else:
                filter_header = [ 'c', 'cc', 'cpp', 'cxx', 'c++',  "java", 'h', 'hpp', 'hh', 'hxx']

            if file_type not in filter_header:
                continue
            
            if self.lsp_function == LSPFunction.Definition and ";" in content:
                continue
            # find character position
            char_pos = content.find(self.symbol_name)
            # the line number is 1-based, we need to convert it to 0-based
            filtered_lines.append((file_path, int(lineno)-1, char_pos))

        print("num of total file: ", len(filtered_lines))
        # shuffle the list to get random files
        random.shuffle(filtered_lines)

        final_resp: list[dict[str, Any]] = []
        all_source_code:list[str] = []

        for file_path, lineno, char_pos in filtered_lines:
            # print("file_path:{}, lineno:{}, char_pos:{}".format(file_path, lineno, char_pos))
            # Define server arguments
            try:
                response = self.fetch_code(file_path,  lineno, int(char_pos))
                for res_json in response:
                    if res_json["source_code"] not in all_source_code:
                        final_resp.append(res_json)
                        all_source_code.append(res_json["source_code"])
                
                if self.lsp_function in [LSPFunction.Declaration, LSPFunction.Definition] and len(final_resp) > 0:
                    return LSPResults.Success.value, final_resp
                
            except Exception as e:
                return f"{LSPResults.Error}: {e}", []
        
        return LSPResults.Success.value, final_resp
    
    def get_file_fucntions(self, file_path: str) -> tuple[str, list[dict[str, Any]]]:

        # 
        path_list: list[Path] =  []
        head_path = Path(file_path).resolve()
        if head_path.suffix in [".c", ".cpp", ".cc"]:
            for ext in [".h", ".hpp", ".hh", ".hxx"]:
                # try to find the header file with the same name
                if head_path.with_suffix(ext).exists():
                    head_path = head_path.with_suffix(ext)
                    path_list.append(head_path)
                    break

            if not path_list:
                for ext in [".h", ".hpp", ".hh", ".hxx"]:
                    # try to find the header file with the same name in different directories
                    file_name = head_path.name.replace(head_path.suffix, ext)
                    # Search the entire workspace for header files with the given name
                    new_list = list(Path(self.project_root).rglob(f"{file_name}"))
                    if new_list:
                        path_list += new_list
                        break
        else:
            path_list.append(Path(file_path).resolve())

        if not path_list:
            return LSPResults.Error.value + f"No Corresponding header file for {file_path} ", []
            
        # for path in path_list:
        # extract all functions from the given file
        
        res_list: list[str] = []
        for _path in path_list:
            parser = self.lang_parser(Path(_path), source_code=None, project_lang=self.project_lang)
            res_list += parser.get_file_functions() # type: ignore

        ret_list: list[dict[str, Any]] = []
         # if the lsp function is related functions, we need to sort the results according to the time it appears in the file
        for src in res_list:

            res_json: dict[str, Any] = {}
            res_json["source_code"] = src

            # extract the function name from the source code
            function_name = extract_name(src)

            # count the number of times the function name appears in the source code
            include_str = ""
            for extin in ["c", "cpp", "cc", "h", "hpp", "hxx", "java"]:
                include_str += f" --include=*.{extin} "
            cmd = f"grep -r {include_str} -o '{function_name}(' {self.project_root} | wc -l"
            
            result = sp.run(cmd, shell=True, stdout=sp.PIPE, stderr=sp.PIPE, text=True)
            if result.returncode == 0:
                # Add the total count across the workspace
                workspace_count = int(result.stdout.strip())
                # Add count information to the result
                res_json["count"] = workspace_count
            else:
                res_json["count"] = 1

            ret_list.append(res_json)

        # sort the results by the count of the function name in the workspace
        ret_list.sort(key=lambda x: x.get("count", 1), reverse=True)
        return LSPResults.Success.value, ret_list

    def get_symbol_info(self) -> tuple[str, list[dict[str, Any]]]:

        """
        Retrieves information about a symbol in the project.
        This method attempts to find the symbol using the LSP server.
        If it fails, it falls back to searching through files in the project directory.
        Returns:
            tuple: A tuple containing a message and a list of dictionaries with symbol information.
        """
        if self.lsp_function == LSPFunction.StructFunctions:
            return self.get_file_fucntions(self.symbol_name)
        else:
            msg, res_list =  self.get_symbol_info_helper()
            return msg, res_list

      
    
def main():
    parser = argparse.ArgumentParser(description='')
    parser.add_argument('--workdir', type=str, default="/home/yk/oss_projects/bind9", help='The search directory.')
    parser.add_argument('--lsp-function', type=str, choices=[e.value for e in LSPFunction], default="declaration", help='The LSP function name')
    parser.add_argument('--symbol-name', type=str, default="dns_rdataclass_in" ,help='The function name or struct name.')
    parser.add_argument('--lang', type=str, choices=[e.value for e in LanguageType], default="C" ,help='The project language.')
    args = parser.parse_args()
    

    lsp = ParserCodeRetriever(args.workdir, LanguageType(args.lang), args.symbol_name, LSPFunction(args.lsp_function))
    try:
        msg, res = lsp.get_symbol_info()
    except Exception as e:
        msg = f"{LSPResults.Error}: {e}"
        res = []

    # print(f"Message: {msg}")
    # print(f"Response: {res}")
    
    if args.lsp_function == LSPFunction.StructFunctions.value:
        file_name = f"{Path(lsp.symbol_name).stem}_struct_functions_parser.json"
    else:
        file_name = f"{lsp.symbol_name}_{lsp.lsp_function.value}_parser.json"
    
    with open(os.path.join("/out", file_name), "w") as f:
        f.write(json.dumps({"message": msg, "response": res}, indent=4))

if __name__ == "__main__":
    main()