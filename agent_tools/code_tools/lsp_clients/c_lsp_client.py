import json
import os
import urllib
from agent_tools.code_tools.lsp_clients.clspclient_raw import ClangdLspClient
from constants import LanguageType, LSPFunction
from typing import Any
from pathlib import Path
from dataclasses import asdict
from agent_tools.code_tools.lsp_clients.extract_functions_clang import LibclangExtractor
import asyncio
import random

class CLSPCLient():
    def __init__(self, workdir: str, project_name: str, project_lang: LanguageType):
   
        self.project_root = workdir
        self.project_name = project_name
        # self.symbol_name = symbol_name
        self.project_lang = project_lang
      

    async def request_fucntion(self, file_path: str, lineno: int, charpos: int, lsp_function: LSPFunction) -> list[dict[str, Any]]:
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
        client = ClangdLspClient(self.project_root, self.project_lang.value.lower())
        await client.start_server()
        await client.initialize()
        # must open the file first
        await client.open_file(file_path)
        
        #  Waiting for clangd to index files
        await client.wait_for_indexing()

        response = []
        if lsp_function == LSPFunction.Declaration:
            # Find declaration
            response = await client.find_declaration(
                file_path,
                line=lineno,  
                character=charpos
            )
            if not response:
                return []

            await client.stop_server()
            result = response.get("result", [])
            return result

        elif lsp_function == LSPFunction.Definition:
            # Find definition
            response = await client.find_definition(
                file_path,
                line=lineno,
                character=charpos
            )
        elif lsp_function == LSPFunction.References:
            
            # Fisrt jump to definition
            response = await client.find_references(
                file_path,
                line=lineno,
                character=charpos
            )
        if not response:
            return []
        await client.stop_server()

        # to keep the same format as multi_lsp_client.py
        return response.get("result", []) # type: ignore
    
    async def request_definition(self, file_path: str, lineno: int, charpos: int) -> list[dict[str, Any]]:
        res = await self.request_fucntion(file_path, lineno, charpos, LSPFunction.Definition)
        return res
    async def request_declaration(self, file_path: str, lineno: int, charpos: int) -> list[dict[str, Any]]:
        res = await self.request_fucntion(file_path, lineno, charpos, LSPFunction.Declaration)
        return res
    async def request_references(self, file_path: str, lineno: int, charpos: int) -> list[dict[str, Any]]:
        res = await self.request_fucntion(file_path, lineno, charpos, LSPFunction.References)
        return res

    def ns_match_length(self, name_space_list: list[str], containerName: str) -> int:
        if not name_space_list:
            return 0
        if not containerName:
            return 0
        
        container_ns = containerName.split("::")
        # match from the last namespace
        for i, (n1, n2) in enumerate(zip(reversed(name_space_list), reversed(container_ns))):
            if n1 != n2:
                return i
        
        return min(len(name_space_list), len(container_ns))

    async def get_workspace_symbol(self, symbol: str="") -> list[tuple[str, int, int]]:
        
        client = ClangdLspClient(self.project_root, self.project_lang.value.lower())
        await client.start_server()
        await client.initialize()
        
        # read complie command
        with open(f"{self.project_root}/compile_commands.json", "r") as f:
            compile_commands = json.load(f)

        random_file: Path = Path("")
        # randomly select a file from the compile_commands.json
        all_indices = list(range(len(compile_commands)))
        random.shuffle(all_indices)

        excluded_dirs = ["build", "external", "third_party"]
        for i in all_indices:
            random_file = Path(compile_commands[i]["directory"]) / compile_commands[i]["file"]
            # to normalize the path ../
            random_file = random_file.resolve()

            # do include build, external, 

            if any(excluded_dir in str(random_file) for excluded_dir in excluded_dirs):
                continue
            if os.path.exists(random_file):
                break

        if not random_file.exists():
            return []
        
        # have to open a file first
        await client.open_file(str(random_file))

        #  Waiting for clangd to index files - increase timeout for workspace indexing
        await client.wait_for_indexing(timeout=5)

        if symbol == "":
            response = await client.find_workspace_symbols("")
        else:
            response = await client.find_workspace_symbols(symbol)

        if response and response.get("result", []):
            result = response.get("result", [])
            await client.stop_server()
            return result
        else:
            print("No workspace symbols found")
            return []

    async def request_workspace_symbol(self, symbol: str="") -> list[tuple[str, int, int, int]]:
     
        # if symbol includes namespace, we need to remove it
        name_space_list = []
        if "::" in symbol:
            symbol_list = symbol.split("::")
            symbol = symbol_list[-1]
            # we only care the last part of the namespace, which usually is the class name or enum
            name_space_list = symbol_list[:-1]

        # Find declaration
        max_retries = 3
        retry_delay = 5.0
        response = None
        
        for attempt in range(max_retries):
            response = await self.get_workspace_symbol(symbol)
            if response:
                break
            # If no results and not the last attempt, wait before retrying
            if attempt < max_retries - 1:
                print(f"Workspace symbols request returned empty, retrying in {retry_delay}s (attempt {attempt + 1}/{max_retries})")
                await asyncio.sleep(retry_delay)
                retry_delay *= 1.5  # Exponential backoff
        
        if not response:
            return []
        # find the one with longest namespace matching
        longest_ns_len = 0
        all_location: list[tuple[str, int, int, int]] = []
        for res in response:

            # Important, LSP will return symbols including the symbol name 
            # eg, if we search for "foo", it will return "foo", "foo1", "foo2", etc
            if res["name"] != symbol: # type: ignore
                continue
            
            # if the symbol has namespace, we need to check if the namespace matches

            ns_length = self.ns_match_length(name_space_list, res.get("containerName", "")) # type: ignore
            if ns_length > longest_ns_len:
                longest_ns_len = ns_length
              
            location = res.get("location", {}) # type: ignore
            if not location:
                continue

            file_path = location.get("uri", "")
            if not file_path:
                continue

            file_path = file_path.replace("file://", "")
            # uri to path
            file_path = urllib.parse.unquote(file_path) # type: ignore
            
            all_location.append((file_path, location['range']['start']['line'], location['range']['start']['character'], ns_length))
        
        # remove the symbols that the namespace length is less than the longest namespace matching
        # the end line is not used for c/c++, so we just set it to start_line + 1
        ret_location = [
            (file_path, line, line+1, charpos) for file_path, line, charpos, ns_length in all_location if ns_length >= longest_ns_len
        ]
        return ret_location


    async def request_all_functions(self) -> list[dict[str, Any]]:
       

        # Create extractor
        extractor = LibclangExtractor(self.project_root, self.project_name)
        # Process project
        extractor.get_all_functions(os.path.join(self.project_root, "compile_commands.json"))
        functions_list = [asdict(func) for func in extractor.extracted_functions.values()]
        return functions_list
    
    async def request_all_headers(self) -> list[dict[str, Any]]:
        """
        Get all header files in the project.
        Returns:
            list[str]: A list of header file paths.
        """

        # Create extractor
        extractor = LibclangExtractor(self.project_root)
        # Process project
        headers = extractor.get_all_headers(os.path.join(self.project_root, "compile_commands.json"))
        
        return [{"file_path": header} for header in headers]