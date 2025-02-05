import subprocess
import threading
import json
import os
import sys
import time
import re
from urllib.parse import unquote, urlparse
import argparse
import subprocess as sp
from clanglsp import ClangdLspClient
import asyncio
import random
from language_parser import LanguageParser
from constants import LanguageType, LSPFunction, LSPResults


class LSPWrapper():
    def __init__(self, workdir: str,  project_lang: LanguageType, symbol_name: str, lsp_function: LSPFunction):
   
        self.project_root = workdir
        self.symbol_name = symbol_name
        self.lsp_function = lsp_function
        self.project_lang = project_lang
      
    def convert_clangd_response(self, response: dict) -> list[dict]:
        """
        Convert the response from clangd to a source code.
        Args:
            response (dict): The response from clangd.
        Returns:
            list[dict]: The list of source code and corresponding file.
        """
     
        # there may be multiple locations for cross-references
        #  'result': [{'range': {'end': {'character': 7, 'line': 122}, 'start': {'character': 2, 'line': 122}}, 'uri': 'file:///src/cjson/cJ                                                                                                           SON.h'}]}
       
        ret_list = []
        for loc in response["result"]:  
            file_path = loc.get("uri", "").replace("file://", "")
            range_start = loc['range']['start']

            parser = LanguageParser(file_path, source_code=None, project_lang=self.project_lang)
            if self.lsp_function == LSPFunction.References:
                # get the full source code of the symbol
                source_code = parser.get_ref_source(self.symbol_name, range_start['line'])
            else:
                source_code = parser.get_symbol_source(self.symbol_name, range_start['line'], self.lsp_function)
            
            if source_code:
                ret_list.append({"source_code": source_code, "file_path": file_path, "line": range_start['line']})

        return ret_list


    async def lsp_c_cpp(self, file_path: str, lineno: int, charpos: int) -> list[dict]:
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
        client = ClangdLspClient(self.project_root, self.project_lang.lower())
        await client.start_server()
        await client.initialize()
        # must open the file first
        await client.open_file(file_path)
        
        #  Waiting for clangd to index files
        await client.wait_for_indexing()

        if self.lsp_function == LSPFunction.Header:
              # Find declaration
            response = await client.find_declaration(
                file_path,
                line=lineno,  
                character=charpos
            )
            await client.stop_server()
            result = response.get("result", [])
            if result:
                loc = result[0]
                file_path = loc.get("uri", "").replace("file://", "")
                # head file doesn't need the source code, it's hard to parse the source code cause the sambol is very different 
                return [{"source_code": "", "file_path": file_path, "line": loc['range']['start']['line']}]
            else:
                return []

        elif self.lsp_function == LSPFunction.Declaration:
            # Find declaration
            response = await client.find_declaration(
                file_path,
                line=lineno,  
                character=charpos
            )

        elif self.lsp_function == LSPFunction.Definition:
            # Find definition
            response = await client.find_definition(
                file_path,
                line=lineno,
                character=charpos
            )
        elif self.lsp_function == LSPFunction.References:
            
            # Fisrt jump to definition
            response = await client.find_references(
                file_path,
                line=lineno,
                character=charpos
            )
      
        await client.stop_server()

        return self.convert_clangd_response(response)

    async def find_all_symbols_c_cpp(self) -> list[tuple[str, int, int]]:
        
        client = ClangdLspClient(self.project_root, self.project_lang.lower())
        await client.start_server()
        await client.initialize()
        
        # read complie command
        with open(f"{self.project_root}/compile_commands.json", "r") as f:
            compile_commands = json.load(f)

        # randomly select a file from the compile_commands.json
        for i in range(len(compile_commands)):
            random_file = os.path.join(compile_commands[i]["directory"], compile_commands[i]["file"])
            random_file = os.path.abspath(random_file)
            if os.path.exists(random_file):
                break

        if not os.path.exists(random_file):
            return f"{LSPResults.Error}:{random_file} does not exist!", []
        
        # have to open a file first
        await client.open_file(random_file)

        #  Waiting for clangd to index files
        await client.wait_for_indexing(timeout=5)

        # Find declaration
        response = await client.find_workspace_symbols(self.symbol_name)

        if not response:
            print("Empty response. Dot close the server, it will stuck")
            return f"{LSPResults.Retry}, Empty Response.", []
        
        print("stop the server")
        await client.stop_server()

        result = response.get("result", [])
        if not result:
            return f"{LSPResults.Error}: result is empty!", []
        
        all_location = []
        for res in result:

            # Important, LSP will return symbols including the symbol name 
            # eg, if we search for "foo", it will return "foo", "foo1", "foo2", etc
            if res["name"] != self.symbol_name:
                continue

            location = res.get("location", {})
            if not location:
                continue

            file_path = location.get("uri", "")
            if not file_path:
                continue

            file_path = file_path.replace("file://", "")
            all_location.append((file_path, location['range']['start']['line'], location['range']['start']['character']))

        return LSPResults.Success, all_location


    async def lsp_java(self, file_path: str, lineno: int, charpos: int) -> list[dict]:
        pass

    # async def get_symbol_info(self) -> list[dict]:
    #     """
    #     Finds information about a given symbol in the project.
    #     This method searches for the specified symbol within the project directory
    #     using the `grep` command. It then attempts to locate the symbol's definition,
    #     declaration, or references using the Language Server Protocol (LSP) for C/C++.
    #     Returns:
    #         list[dict]: A list of dictionaries containing information about the symbol.
    #             If the symbol is not found, an empty string is returned.
    #     """
        
    #     # Execute `find` command to recursively list files and directories
    #     cmd = f"grep --binary-files=without-match -rnw {self.project_root} -e  {self.symbol_name}"
    #     results = sp.run(cmd, shell=True, stdout=sp.PIPE, stderr=sp.STDOUT,  text=True)
    #     output = results.stdout.strip()

    #     if not output:
    #         return []

    #     # We have to find a location of the symbol in the source code to pass to the LSP server.
    #     # sometimes the location may be in the comments or in the string literals
    #     max_try = 10

    #      # find the file path, line number and character position
    #     all_lines =  output.splitlines()
    #     random.shuffle(all_lines)
    #     print("num of total file: ", len(all_lines))

    #     # filter some files by file type
    #     filtered_lines = []
    #     for line in all_lines:
            
    #         parts = line.split(':', 2)
    #         # check if the line is valid
    #         if len(parts) < 3:
    #             continue

    #         file_path, lineno, content = parts
    #         # filter the .h file and other files (.md, .txt, etc)
    #         file_type = file_path.split('.')[-1]
    #         if file_type not in [ 'c', 'cc', 'cpp', 'cxx', 'c++', "java"]:
    #             continue
            
    #         # find character position
    #         char_pos = content.find(self.symbol_name)
    #         filtered_lines.append((file_path, lineno, char_pos))

    #     final_resp = []
    #     all_source_code = []
    #     for i, (file_path, lineno, char_pos) in enumerate(filtered_lines[:max_try]):
    #         print("try:{}, file_path:{}, lineno:{}, char_pos:{}".format(i, file_path, lineno, char_pos))

    #         # Define server arguments
    #         try:
    #             if self.project_lang in [LanguageType.C, LanguageType.CPP]:
    #                 response = await self.lsp_c_cpp(file_path,  int(lineno)-1, int(char_pos))
    #             elif self.project_lang == LanguageType.JAVA:
    #                 response = await self.lsp_java(file_path,  int(lineno)-1, int(char_pos))


    #             for res_json in response:
    #                 if res_json["source_code"] not in all_source_code:
    #                     final_resp.append(res_json)
    #                     all_source_code.append(res_json["source_code"])
                
    #         except Exception as e:
    #             print(f"Error: {e}")
        
    #     return final_resp

    async def get_symbol_info(self) -> list[dict]:
        """
        Finds information about a given symbol in the project.
        This method searches for the specified symbol within the project directory
        using the `grep` command. It then attempts to locate the symbol's definition,
        declaration, or references using the Language Server Protocol (LSP) for C/C++.
        Returns:
            list[dict]: A list of dictionaries containing information about the symbol.
                If the symbol is not found, an empty string is returned.
        """
        if self.project_lang in [LanguageType.C, LanguageType.CPP]:
            msg, all_symbols = await self.find_all_symbols_c_cpp()
            
            if len(all_symbols) == 0:
                return msg, []

        # all_symbols should be only one
        if len(all_symbols) > 1:
            return f"{LSPResults.Error}: More than one symbol found. {all_symbols}", all_symbols
        
        # We have to find a location of the symbol in the source code to pass to the LSP server.
        # sometimes the location may be in the comments or in the string literals
         # find the file path, line number and character position
        print("num of total file: ", len(all_symbols))

        final_resp = []
        all_source_code = []
        file_path, lineno, char_pos = all_symbols[0]

        # Define server arguments
        try:
            if self.project_lang in [LanguageType.C, LanguageType.CPP]:
                response = await self.lsp_c_cpp(file_path,  int(lineno), int(char_pos))
            elif self.project_lang == LanguageType.JAVA:
                response = await self.lsp_java(file_path,  int(lineno), int(char_pos))

            for res_json in response:
                if res_json["source_code"] not in all_source_code:
                    final_resp.append(res_json)
                    all_source_code.append(res_json["source_code"])
        except Exception as e:
            print(f"Error: {e}")
            return f"{LSPResults.Error}: {e}", []
        
        return LSPResults.Success, final_resp

def debug_environment():
    print("Current working directory:", os.getcwd())
    print("Environment variables:", dict(os.environ))
    print("Current user/group:", os.getuid(), os.getgid())
    print("Directory contents:", os.listdir('.'))
    print("Compile commands exists:", os.path.exists(os.path.join(os.getcwd(),"compile_commands.json" )))

    
async def main():
    parser = argparse.ArgumentParser(description='')
    parser.add_argument('--workdir', type=str, help='The work place that can run bear compile.')
    parser.add_argument('--lsp-function', type=str, choices=[LSPFunction.Definition, LSPFunction.Declaration, LSPFunction.References, LSPFunction.Header], help='The LSP function name')
    parser.add_argument('--symbol-name', type=str, help='The function name or struct name.')
    parser.add_argument('--lang', type=str, choices=[LanguageType.C, LanguageType.CPP,  LanguageType.JAVA], help='The project language.')
    parser.add_argument('--seperator', type=str, default="="*50, help='The seperator used to separate the response.')
    args = parser.parse_args()

    lsp = LSPWrapper(args.workdir, args.lang, args.symbol_name, args.lsp_function)
    msg, res = await lsp.get_symbol_info()

    # print the response to the screen, the seperator is used to separate the response
    # subprocess.run(["clear"])
    print(args.seperator)
    print(msg)
    print(args.seperator)
    print(json.dumps(res))

if __name__ == "__main__":
    asyncio.run(main())