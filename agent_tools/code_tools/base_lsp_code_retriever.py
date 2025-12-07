import json
import os
import argparse
from agent_tools.code_tools.lsp_clients.c_lsp_client import CLSPCLient
from agent_tools.code_tools.lsp_clients.multi_lsp_client import MultilspyClient
import asyncio
from agent_tools.code_tools.parsers.c_parser import CParser
from agent_tools.code_tools.parsers.cpp_parser import CPPParser
from agent_tools.code_tools.parsers.java_parser import JavaParser
from agent_tools.code_tools.parsers.python_parser import PythonParser
from constants import LanguageType, LSPFunction, LSPResults
from typing import Any
from pathlib import Path
import shutil
import urllib

class BaseLSPCodeRetriever():
    def __init__(self, workdir: str, project_name: str, project_lang: LanguageType, symbol_name: str, lsp_function: LSPFunction):
   
        self.project_root = workdir
        self.project_name = project_name
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
        elif self.project_lang == LanguageType.Python:
            return PythonParser
        else:
            raise Exception(f"Language {self.project_lang} not supported.")
    
    def get_lsp_client(self):
        if self.project_lang in [LanguageType.C, LanguageType.CPP]:
            return CLSPCLient(self.project_root, self.project_name, self.project_lang)
        else:
            return MultilspyClient(self.project_root, self.project_name, self.project_lang) 
        
    def fectch_code(self, file_path: str, start_line: int, lsp_function: LSPFunction) -> list[dict[str, Any]]:

        query_key = ""
        start_line = 0
        parser = self.lang_parser(Path(file_path), source_code=None)
        if lsp_function == LSPFunction.References:
            # get the full source code of the symbol
            source_code = parser.get_ref_source(self.symbol_name, start_line) # type: ignore
            if source_code == "":
                return []
            return [{"source_code": source_code, "file_path": file_path, "type": query_key, "start_line": start_line}]
      
        # for declaration and definition, we need to get the symbol name without namespace
        if "::" in self.symbol_name:
            symbol_name = self.symbol_name.split("::")[-1]
        else:
            symbol_name = self.symbol_name
        # since we have match the namespace when finding the symbol, there is no need to match the namespace again
        # the namespace matching in the parser sometimes will fail, so we just use the symbol name directly
        query_key, source_code, start_line = parser.get_symbol_source(symbol_name, start_line, lsp_function)

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
            start_line = max(start_line-5, 0)
            end_line = min(len(lines), start_line + 50)
            source_code = "\n".join(lines[start_line:end_line])

        return [{"source_code": source_code, "file_path": file_path, "type": query_key, "start_line": start_line}]

    def fectch_code_from_response(self, response: list[dict[str, Any]], lsp_function: LSPFunction) -> list[dict[str, Any]]:
        raise NotImplementedError("This method should be implemented in subclasses.")
       

    async def request_function(self, file_path: str, start_line: int, end_line: int, charpos: int) -> list[dict[str, Any]]:
        """
        Args:
            file_path (str): The C++ source file including the symbol.
            start_line (int): The line number where the symbol is located.
            end_line (int): The ending line number where the symbol is located.
            charpos (int): The character position within the line where the symbol is located.
        Returns:
            list[dict]: The list of source code and corresponding file.
        Raises:
            Exception: If there is an error during the request to the LSP server.
        """
        raise NotImplementedError("This method should be implemented in subclasses.")

    async def get_all_functions(self) -> tuple[str, list[dict[str, Any]]]:
        raise NotImplementedError("This method should be implemented in subclasses.")


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
      
        # Find declaration
        all_symbols = await self.lsp_client.request_workspace_symbol(self.symbol_name)

        if len(all_symbols) == 0:
            return LSPResults.NoSymbol.value, []
      
        # all_symbols should be only one
        # if len(all_symbols) > 1:
            # return f"{LSPResults.Error}: More than one symbol found. {all_symbols}", []
        
        print("num of total file: ", len(all_symbols))
        final_resp: list[dict[str, Any]] = []

        for file_path, start_line, end_line, char_pos in all_symbols:

            if file_path.startswith("/usr/include") or file_path.startswith("/usr/local/include"):
                # print(f"Skip system header file: {file_path}")
                continue
            print("file_path:{}, start_line:{}, char_pos:{}".format(file_path, start_line, char_pos))

            # continue
            # Define server arguments
            try:
                response = await self.request_function(file_path,  int(start_line), int(end_line), int(char_pos))

                final_resp += response
            except Exception as e:
                print(f"Error: {e}")
                return LSPResults.Error.value + f": {e}", []

        return LSPResults.Success.value, final_resp

