import json
import os
import argparse

from openai import project
from agent_tools.code_tools.lsp_clients.c_lsp_client import CLSPCLient
from agent_tools.code_tools.lsp_clients.multi_lsp_client import MultilspyClient
import asyncio
from agent_tools.code_tools.parsers.c_parser import CParser
from agent_tools.code_tools.parsers.cpp_parser import CPPParser
from agent_tools.code_tools.parsers.java_parser import JavaParser
from agent_tools.code_tools.parsers.python_parser import PythonParser
from agent_tools.code_tools.base_lsp_code_retriever import BaseLSPCodeRetriever
from constants import LanguageType, LSPFunction, LSPResults
from typing import Any
from pathlib import Path
import shutil
import urllib

class CPPLSPCodeRetriever(BaseLSPCodeRetriever):
    def __init__(self, workdir: str, project_name: str, project_lang: LanguageType, symbol_name: str, lsp_function: LSPFunction):
        super().__init__(workdir, project_name, project_lang, symbol_name, lsp_function)

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


    async def request_function(self, file_path: str, start_line: int, end_line: int, charpos: int) -> list[dict[str, Any]]:
        """
        Args:
            file_path (str): The C++ source file including the symbol.
            start_line (int): The line number where the symbol is located.
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
            response = await self.lsp_client.request_declaration(file_path, lineno=start_line, charpos=charpos)
            resp_list =  self.fectch_code_from_response(response, self.lsp_function)
            resp_list += self.fectch_code(file_path, start_line, self.lsp_function)
            return resp_list
    
        elif self.lsp_function == LSPFunction.Definition:
            response = await self.lsp_client.request_definition(file_path, lineno=start_line, charpos=charpos)
            
            resp_list =  self.fectch_code_from_response(response, self.lsp_function)
            resp_list += self.fectch_code(file_path, start_line, self.lsp_function)
            return resp_list
        
        elif self.lsp_function == LSPFunction.References:
            response = await self.lsp_client.request_references(file_path, lineno=start_line, charpos=charpos)
            return self.fectch_code_from_response(response, self.lsp_function)
        else:
            return []
            # raise Exception(f"Unsupported LSP function: {self.lsp_function}")

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
        response = await self.lsp_client.request_all_headers() # type: ignore

        if not response:
            return f"{LSPResults.Error.value}, Empty Response.", [{}]

        return LSPResults.Success.value, response # type: ignore

async def get_response_helper(workdir: str,project_name: str, lang: str, symbol_name: str, lsp_function: str) -> tuple[str, list[dict[str, Any]]]:
        # the default workdir is the current directory, since we didn't send the compile_comamnd.json to the clangd server
    lsp = CPPLSPCodeRetriever(workdir, project_name, LanguageType(lang), symbol_name, LSPFunction(lsp_function))

    if lsp_function == LSPFunction.AllSymbols.value:
        msg, res = await lsp.get_all_functions()
    elif lsp_function == LSPFunction.AllHeaders.value:
        msg, res = await lsp.get_all_headers()
    else:
        msg, res = await lsp.get_symbol_info()

    return msg, res

async def get_cpp_response(workdir: str, project: str,  lang: str, symbol_name: str, lsp_function: str) -> tuple[str, list[dict[str, Any]]]:
   
    msg, res = await get_response_helper(workdir, project, lang, symbol_name, lsp_function)
    # if the workdir is not the same as /src/project, we will retry with
    if msg == LSPResults.NoSymbol.value:
        for src_path in ["/src/{}".format(project), "/src"]:
            if msg == LSPResults.NoSymbol.value and workdir != src_path and os.path.exists(src_path):
            
                # TODO does the compile_commands.json really exist in the workdir?
                # may need locate it first
                # copy the compile_commands.json to the src_path
                if os.path.exists(f"{workdir}/compile_commands.json"):
                    shutil.copy(f"{workdir}/compile_commands.json", f"{src_path}/compile_commands.json")
                print(f"Retry with src path: {src_path}")
                # the default workdir is the current directory, since we didn't send the compile_comamnd.json to the clangd server
                msg, res = await get_response_helper(src_path, project, lang, symbol_name, lsp_function)

    return msg, res 