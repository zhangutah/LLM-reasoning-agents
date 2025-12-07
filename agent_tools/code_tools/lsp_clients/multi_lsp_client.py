from urllib import response
from multilspy import LanguageServer
from multilspy.multilspy_config import MultilspyConfig
from multilspy.multilspy_logger import MultilspyLogger
from constants import LanguageType
import subprocess as sp
from typing import Any
import urllib
from pathlib import Path

class MultilspyClient():
    def __init__(self, workdir: str, project_name: str, project_lang: LanguageType):
      
        if project_lang == LanguageType.JAVA:
            self.config = MultilspyConfig.from_dict({"code_language": "java"})
        else:
            self.config = MultilspyConfig.from_dict({"code_language": project_lang.value})
        self.lsp = LanguageServer.create(self.config,  MultilspyLogger(), workdir)
        self.project_lang = project_lang

    async def request_definition(self, file_path: str, lineno: int, charpos: int) -> list[dict[str, Any]]:
        '''
        For java, the response is useless like :
        [{'uri': 'file:///src/args4j/args4j/src/org/kohsuke/args4j/CmdLineParser.java', 
        'range': {'start': {'line': 113, 'character': 16}, 'end': {'line': 113, 'character': 27}}, 
        'absolutePath': '/src/args4j/args4j/src/org/kohsuke/args4j/CmdLineParser.java',
         'relativePath': 'args4j/src/org/kohsuke/args4j/CmdLineParser.java'}]
        '''
        async with self.lsp.start_server():
            result = await self.lsp.request_definition(file_path, lineno, charpos)
        return result
    
    async def request_declaration(self, file_path: str, lineno: int, charpos: int) -> list[dict[str, Any]]:
        '''
        For java, the response is like the following, but it only extract the method signature:
        {'contents': [{'language': 'java', 
        'value': 'void org.kohsuke.args4j.CmdLineParser.addArgument(Setter setter, Argument a)'}, 
        'Programmatically defines an argument']}
        '''
        async with self.lsp.start_server():
            # {"contents":[{"value":"source code","language":"java"}]}
            result = await self.lsp.request_hover(file_path, lineno, charpos)
        return result
    
    async def request_completions(self, file_path: str, lineno: int, charpos: int) -> list[dict[str, Any]]:
        async with self.lsp.start_server():
            result = await self.lsp.request_completions(file_path, lineno, charpos)
        return result
    

    async def request_references(self, file_path: str, lineno: int, charpos: int) -> list[dict[str, Any]]:
        '''
        For java, the response is like:
        [{'uri': 'file:///src/args4j/args4j/src/org/kohsuke/args4j/ClassParser.java',
        'range': {'start': {'line': 26, 'character': 27}, 'end': {'line': 26, 'character': 74}}, 
        'absolutePath': '/src/args4j/args4j/src/org/kohsuke/args4j/ClassParser.java', 
        'relativePath': 'args4j/src/org/kohsuke/args4j/ClassParser.java'}, ...]
        '''
        async with self.lsp.start_server():
            result = await self.lsp.request_references(file_path, lineno, charpos)
        return result
    
    async def request_document_symbols(self, file_path: str) -> list[dict[str, Any]]:
        async with self.lsp.start_server():
            result = await self.lsp.request_document_symbols(file_path)
        return result
    
    async def request_hover(self, file_path: str, lineno: int, charpos: int) -> list[dict[str, Any]]:
        async with self.lsp.start_server():
            result = await self.lsp.request_hover(file_path, lineno, charpos)
        return result
    
    async def request_all_headers(self) -> list[dict[str, Any]]:
        return []
    async def request_all_functions(self) -> list[dict[str, Any]]:
        return []
       
    # not working well for java now, class works but method not working
    async def get_workspace_symbols(self, symbol: str="") -> list[tuple[str, int, int]]:
        async with self.lsp.start_server():
            result = await self.lsp.request_workspace_symbol(symbol)
        return result

    async def get_workspace_symbol_java(self, symbol: str, simplified_name: str, filtered_file: set[str]) -> list[tuple[str, int, int, int]]:
        # split the qualified name into namespace and pure name
        
        # if the last name is method, then the second last is the class name
        if '.' in symbol:
            class_name = symbol.split('.')[-2]
        else:
            class_name = symbol
     
        # short cut way to find the symbol file, reduce the search space
        all_files: list[str] = []
        for file_path in filtered_file:
            filename = Path(file_path).stem
            # class name should match the file name
            if filename not in [simplified_name, class_name]: 
                continue
            all_files.append(file_path)
            break
          
        if not all_files:
            all_files = list(filtered_file)
        
        file_symbols_dict: dict[str, dict[str, Any]] = {}
        async with self.lsp.start_server(): # type: ignore
            for file_path in all_files:
                result = await self.lsp.request_document_symbols(file_path)

                if not result:
                    continue
                # result[1] is None if no symbols found
                file_symbols_dict[file_path] = result[0] # type: ignore
        # find the one with longest namespace matching
       
        all_location: list[tuple[str, int, int, int]] = []
        for file_path, symbol_list in file_symbols_dict.items():
            for symbol_dict in symbol_list:
              
                # for java, the symbol name may contain parameters, if it is a method
                if symbol_dict["kind"] in [6, 8]:  # method
                    name_only = symbol_dict["name"].split('(')[0]
                else:
                    name_only = symbol_dict["name"]
                
                if simplified_name != name_only:
                    continue
        
                if "range" not in symbol_dict:
                    continue
                print(symbol_dict)
                all_location.append((file_path, symbol_dict['range']['start']['line'],
                 symbol_dict['range']['end']['line'], symbol_dict['range']['start']['character']))
            
        print("All found locations:", all_location)
        return all_location

    async def request_workspace_symbol(self, symbol: str) -> list[tuple[str, int, int, int]]:

        simplified_name = symbol
        if self.project_lang == LanguageType.JAVA and "." in symbol:
            simplified_name = symbol.split('.')[-1]

        # Execute `find` command to recursively list files and directories
        cmd = f"grep --binary-files=without-match -rnw /src -e  {simplified_name}"

        results = sp.run(cmd, shell=True, stdout=sp.PIPE, stderr=sp.STDOUT,  text=True)
        output = results.stdout.strip()

        if not output:
            return []

        # the location may be in the comments or in the string literals
        all_lines = output.splitlines()

        # filter some files by file type
        filtered_file: set[str] = set()
        for line in all_lines:
            
            parts = line.split(':', 2)
            # check if the line is valid
            if len(parts) < 3:
                continue

            file_path, _, _ = parts
            # filter the other files (.md, .txt, etc)
            file_type = file_path.split('.')[-1]
            filter_header = ["java", "py"]

            if file_type not in filter_header:
                continue
            filtered_file.add(file_path)

        if self.project_lang == LanguageType.JAVA:
            return await self.get_workspace_symbol_java(symbol, simplified_name, filtered_file)
