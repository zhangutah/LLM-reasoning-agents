import json
import os
import argparse
from agent_tools.code_tools.lsp_clients.c_lsp_client import CLSPCLient
from agent_tools.code_tools.lsp_clients.multi_lsp_client import MultilspyClient
import asyncio
from agent_tools.code_tools.parsers.c_parser import CParser
from agent_tools.code_tools.parsers.cpp_parser import CPPParser
from agent_tools.code_tools.parsers.java_parser import JavaParser
from constants import LanguageType, LSPFunction, LSPResults
from typing import Any
from pathlib import Path
import shutil
import urllib

class LSPCodeRetriever():
    def __init__(self, workdir: str,  project_lang: LanguageType, symbol_name: str, lsp_function: LSPFunction):
   
        self.project_root = workdir
        self.symbol_name = symbol_name
        self.lsp_function = lsp_function
        self.project_lang = project_lang
        self.lang_parser = self.get_language_parser()
        self.lsp_client = self.get_lsp_client()

    def get_language_parser(self):
        if self.project_lang in [LanguageType.C]:
            return CParser
        elif self.project_lang in [LanguageType.CPP]:
            return CPPParser
        elif self.project_lang == LanguageType.JAVA:
            return JavaParser
        else:
            raise Exception(f"Language {self.project_lang} not supported.")
    
    def get_lsp_client(self):
        if self.project_lang in [LanguageType.C, LanguageType.CPP]:
            return CLSPCLient(self.project_root, self.project_lang)
        else:
            return MultilspyClient(self.project_root, self.project_lang) 
        
    def fectch_code(self, file_path: str, lineno: int, lsp_function: LSPFunction) -> list[dict[str, Any]]:

        query_key = ""
        start_line = 0
        parser = self.lang_parser(Path(file_path), source_code=None)
        if lsp_function == LSPFunction.References:
            # get the full source code of the symbol
            source_code = parser.get_ref_source(self.symbol_name, lineno) # type: ignore
            if source_code == "":
                return []
            return [{"source_code": source_code, "file_path": file_path, "line": lineno, "type": query_key, "start_line": start_line}]
      
        # for declaration and definition, we need to get the symbol name without namespace
        if "::" in self.symbol_name:
            symbol_name = self.symbol_name.split("::")[-1]
        else:
            symbol_name = self.symbol_name
        # since we have match the namespace when finding the symbol, there is no need to match the namespace again
        # the namespace matching in the parser sometimes will fail, so we just use the symbol name directly
        query_key, source_code, start_line = parser.get_symbol_source(symbol_name, lineno, lsp_function)

        if lsp_function == LSPFunction.Declaration:
            # template header file, return empty
            file_text  = Path(file_path).read_text(encoding="utf-8")
            if source_code == "" and self.symbol_name not in file_text:
                # if the source code is not found, we will return the full line of the file
                return []
            
        # for definition and declaration, if we can't find the source code, we will return 50 lines around the lineno
        # since the location must be correct, the empty source code means the parser failed to find the symbol
        if source_code == "":
            # return 50 lines after lineno
            lines = Path(file_path).read_text(encoding="utf-8").splitlines()
            start_line = min(lineno-5, 0)
            end_line = min(len(lines), lineno + 45)
            source_code = "\n".join(lines[start_line:end_line])

        return [{"source_code": source_code, "file_path": file_path, "line": lineno, "type": query_key, "start_line": start_line}]

    def fectch_code_from_response(self, response: list[dict[str, Any]], lsp_function: LSPFunction) -> list[dict[str, Any]]:
        """
        Convert the response from clangd to a source code.
        Args:
            response (dict): The response from clangd.
        Returns:
            list[dict]: The list of source code and corresponding file.
        """
     
        # there may be multiple locations for cross-references
        #  [{'range': {'end': {'character': 7, 'line': 122}, 'start': {'character': 2, 'line': 122}}, 'uri': 'file:///src/cjson/cJ                                                                                                           SON.h'}]}
        if not response:
            return []
        
        ret_list: list[dict[str, Any]] = []
        for loc in response:  
            file_path = loc.get("uri", "").replace("file://", "")
            # convert uri to file path
            file_path = urllib.parse.unquote(file_path) # type: ignore
            range_start = loc['range']['start']

            source_dict = self.fectch_code(file_path, range_start['line'], lsp_function)
            ret_list += source_dict 

        return ret_list


    async def request_function(self, file_path: str, lineno: int, charpos: int) -> list[dict[str, Any]]:
        """
        Args:
            file_path (str): The C++ source file including the symbol.
            lineno (int): The line number where the symbol is located.
            charpos (int): The character position within the line where the symbol is located.
        Returns:
            list[dict]: The list of source code and corresponding file.
        Raises:
            Exception: If there is an error during the request to the LSP server.
        """
        
        response = []
        # Find declaration
        # for C/C++, the lsp is very strange, if the symbol already is the declaration, request_declaration will return the definition
        # if the symbol is already a definition, definition will return the declaration
        # this will cause the parser to fail to find the declaration or the definition, so we also need to parser the symbol location to make sure
        # we get the correct declaration or definition, this may return multiple definitions for rare cases like "typdef struct"

        if self.lsp_function == LSPFunction.Declaration:
            response = await self.lsp_client.request_declaration(file_path, lineno=lineno, charpos=charpos)
            resp_list =  self.fectch_code_from_response(response, self.lsp_function)
            resp_list += self.fectch_code(file_path, lineno, self.lsp_function)
            return resp_list
    
        elif self.lsp_function == LSPFunction.Definition:
            response = await self.lsp_client.request_definition(file_path, lineno=lineno, charpos=charpos)
            
            resp_list =  self.fectch_code_from_response(response, self.lsp_function)
            resp_list += self.fectch_code(file_path, lineno, self.lsp_function)
            return resp_list
        
        elif self.lsp_function == LSPFunction.References:
            response = await self.lsp_client.request_references(file_path, lineno=lineno, charpos=charpos)
            return self.fectch_code_from_response(response, self.lsp_function)
        else:
            return []
            # raise Exception(f"Unsupported LSP function: {self.lsp_function}")

    async def find_all_symbols(self) -> tuple[str, list[tuple[str, int, int]]]:
        
        # Find declaration
        response = await self.lsp_client.request_workspace_symbols(self.symbol_name)

        if not response:
            return LSPResults.NoSymbol.value, []

        return LSPResults.Success.value, response

    async def get_all_functions(self) -> tuple[str, list[dict[str, Any]]]:
        
        # Find declaration
        response = await self.lsp_client.request_all_functions()

        if not response:
            return f"{LSPResults.Error.value}, Empty Response.", []

        return LSPResults.Success.value, response
    
    async def get_all_headers(self) -> tuple[str, list[dict[str, Any]]]:
        """
        Get all header files in the project.
        Returns:
            tuple[str, list[dict[str, Any]]]: A tuple containing a message and a list of header file paths.
        """
        response = await self.lsp_client.request_all_headers()

        if not response:
            return f"{LSPResults.Error.value}, Empty Response.", [{}]

        return LSPResults.Success.value, response

    async def get_symbol_info(self) -> tuple[str, list[dict[str, Any]]]:
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
            msg, all_symbols = await self.find_all_symbols()
            
            if len(all_symbols) == 0:
                return msg, []
        else:
            return LSPResults.Error.value + ", Unsupported language.", []
          
        # all_symbols should be only one
        # if len(all_symbols) > 1:
            # return f"{LSPResults.Error}: More than one symbol found. {all_symbols}", []
        
        print("num of total file: ", len(all_symbols))

        final_resp: list[dict[str, Any]] = []

        for file_path, lineno, char_pos in all_symbols:

            if file_path.startswith("/usr/include") or file_path.startswith("/usr/local/include"):
                # print(f"Skip system header file: {file_path}")
                continue
            print("file_path:{}, lineno:{}, char_pos:{}".format(file_path, lineno, char_pos))

            # continue
            # Define server arguments
            try:
                response = await self.request_function(file_path,  int(lineno), int(char_pos))

                final_resp += response
            except Exception as e:
                print(f"Error: {e}")
                return LSPResults.Error.value + f": {e}", []

        return LSPResults.Success.value, final_resp


async def get_response(workdir: str, lang: str, symbol_name: str, lsp_function: str) -> tuple[str, list[dict[str, Any]]]:
        # the default workdir is the current directory, since we didn't send the compile_comamnd.json to the clangd server
    lsp = LSPCodeRetriever(workdir, LanguageType(lang), symbol_name, LSPFunction(lsp_function))

    if lsp_function == LSPFunction.AllSymbols.value:
        msg, res = await lsp.get_all_functions()
    elif lsp_function == LSPFunction.AllHeaders.value:
        msg, res = await lsp.get_all_headers()
    else:
        msg, res = await lsp.get_symbol_info()

    return msg, res
    
async def main():
    parser = argparse.ArgumentParser(description='')
    parser.add_argument('--project', type=str, default="cppcheck", help='The project name.')
    parser.add_argument('--workdir', type=str, default="/src/cppcheck", help='The work place that can run bear compile.')
    parser.add_argument('--lsp-function', type=str, default="declaration", choices=[e.value for e in LSPFunction], help='The LSP function name')
    parser.add_argument('--symbol-name', type=str, default="CppCheck::check", help='The function name or struct name.')
    parser.add_argument('--lang', type=str, default="CPP", choices=[e.value for e in LanguageType], help='The project language.')
    args = parser.parse_args()

    msg, res = await get_response(args.workdir, args.lang, args.symbol_name, args.lsp_function)
    # if the workdir is not the same as /src/project, we will retry with
    for src_path in ["/src/{}".format(args.project), "/src"]:
        if msg == LSPResults.NoSymbol.value and args.workdir != src_path and os.path.exists(src_path):
            # copy the compile_commands.json to the src_path
            if os.path.exists(f"{args.workdir}/compile_commands.json"):
                shutil.copy(f"{args.workdir}/compile_commands.json", f"{src_path}/compile_commands.json")
            print(f"Retry with src path: {src_path}")
            # the default workdir is the current directory, since we didn't send the compile_comamnd.json to the clangd server
            msg, res = await get_response(src_path, args.lang, args.symbol_name, args.lsp_function)

    file_name = f"{args.symbol_name}_{args.lsp_function}_lsp.json"
    with open(os.path.join("/out", file_name), "w") as f:
        f.write(json.dumps({"message": msg, "response": res}, indent=4))

if __name__ == "__main__":
    asyncio.run(main())
