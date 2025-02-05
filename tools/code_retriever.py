from utils.docker_utils import DockerUtils
from constants import ToolDescMode, LanguageType, LSPFunction
import json
import os
import shutil
import logging
import subprocess as sp
from constants import LSPResults

header_desc_mapping = {
        ToolDescMode.Simple: """
        get_symbol_header(symbol_name)-> str:

        this function can find the header file that declare the symbol name.
        :param symbol_name: The symbol name like class name, function name, struct name etc.
        :return: Full path to the header file if found, otherwise None.
        """,
        ToolDescMode.Detailed:  """
        get_symbol_header(symbol_name)-> str:
        
        this function can find the header file that declare the symbol name. Please keep the namespace of the symbol name if they have.
        :param symbol_name: The symbol name like class name, function name, struct name etc.
        :return: Full path to the header file if found, otherwise None.

        Example:
            get_symbol_header("cJSON") -> "../cJSON.h"
            get_symbol_header("ada::url ada::parser::parse_url<ada::url>") -> "ada.h"

        """
    }

import functools

def catch_exception(func):
    @functools.wraps(func)
    def wrapper(self, *args, **kwargs):
        try:
            return func(self, *args, **kwargs)
        except Exception as e:
            self.logger.error(f"Error in {func.__name__}: {str(e)}")
            return None
    return wrapper

class CodeRetriever():
    # TODO: how to deal with the same symbol name in different files, we may not know the file path
    '''
    This class is used to retrieve the code information for a project through LSP and language parser.
    It run a python file lsp_wrapper inside the docker container to get the code information. Therefore, those python files should be copied to the docker container first.
    The python file implementes the LSP client to interact with different LSP servers (clangd for c/c++, ) to get the following information:
    1. Header file path for a symbol name.
    2. Symbol declaration for a symbol name.
    3. Symbol definition for a symbol name.
    4. Symbol cross reference for a symbol name.
    '''

    def __init__(self, ossfuzz_dir: str, project_name: str, new_project_name: str, project_lang: LanguageType, cache_dir: str, logger: logging.Logger):

        self.ossfuzz_dir = ossfuzz_dir
        self.project_name = project_name
        self.new_project_name = new_project_name
        self.project_lang = project_lang
        self.cache_dir = cache_dir
        self.logger = logger
        self.docker_tool = DockerUtils(self.ossfuzz_dir, self.project_name, self.new_project_name, self.project_lang)

    @catch_exception
    def view_code(self, file_path: str, line_start: int, line_end: int) -> str:
        """
        Reads a specific portion of code from the file path.
        Args:
            file_path (str): The path to the file to read from.
            line_start (int): The starting line number (0-indexed).
            line_end (int): The ending line number (0-indexed).
        Returns:
            str: The extracted code as a string.
        """
        # Read the file from the docker container
        def read_file_callback(container):
            cmd = f"sed -n '{line_start + 1},{line_end + 1}p' {file_path}"
            return container.exec_run(cmd)

        result = self.docker_tool.run_docker_cmd(read_file_callback)

        # sed 
        if "sed: " in result:
            self.logger.warning(result)
            return ""
        return result
        

    @catch_exception
    def get_symbol_info(self, symbol_name: str, lsp_function: LSPFunction) -> list[dict]:
        """
        Retrieves the declaration information of a given symbol using the Language Server Protocol (LSP).
        Args:
            symbol_name (str): The name of the symbol for which to retrieve the declaration.
        Returns:
             list[dict]: [{"source_code":"", "file_path":"", "line":""}]
        """

        save_path = os.path.join(self.cache_dir, self.project_name,f"{symbol_name}_{lsp_function}.json")
        # get the lsp response from the cache if it exists
        if self.cache_dir and os.path.exists(save_path):
            self.logger.info(f"Getting {lsp_function} for {symbol_name} from cache")
            with open(save_path, "r") as f:
                return json.load(f)

        # if no compile_commands.json, we need to install bear to generate it
        # we assume that the project has bear, and clangd installed
        def lsp_call_back(container):
        
            # Get the working directory
            container_info = container.attrs 
            workdir = container_info['Config'].get('WorkingDir', '')

            json_path = os.path.join(self.cache_dir, self.project_name, "compile_commands.json")
            
            try:
                sp_result = sp.run(f"docker cp {json_path} {container.id}:{workdir}", shell=True, check=True, stdout=sp.DEVNULL, stderr=sp.DEVNULL)
            except Exception as e:
                return f"Error: {e}"
            
            # compile_text = self.docker_tool.contrainer_exec_run(container, "cat compile_commands.json")
            # if "No such file or directory" in compile_text:
                # return "Error: No compile_commands.json found"          
            cmd_list = ["python", "lsp_wrapper.py", 
                        "--workdir", workdir, 
                        "--lsp-function", lsp_function,
                        "--symbol-name", symbol_name, 
                        "--lang", self.project_lang.upper()]
            
            retry = 3
            for i in range(retry):
                res_str = self.docker_tool.contrainer_exec_run(container, cmd_list)
                
                # docker run error 
                if res_str.startswith(LSPResults.DockerError):
                    return res_str
                # docker run success, read LSP response
                # split the result
                _, msg, res_dict = res_str.split("="*50)
                msg = msg.strip()
                if not msg.startswith(LSPResults.Retry):
                    return res_dict
        
            return f"{LSPResults.Error} {retry} times retry failed"
          
        
        self.logger.info(f"Calling lsp_wrapper to get {lsp_function} for {symbol_name}")
        res_str = self.docker_tool.run_docker_cmd(lsp_call_back)
       
        if res_str.startswith(LSPResults.DockerError) or res_str.startswith(LSPResults.Error):
            self.logger.error(f"Error in when calling lsp_wrapper: {res_str}")
            return []
        
        # no error, parse the response
        lsp_resp = json.loads(res_str)
        if self.cache_dir:
            if not os.path.exists(f"{self.cache_dir}/{self.project_name}"):
                os.makedirs(f"{self.cache_dir}/{self.project_name}")

            # TODO: same symbol name
            with open(save_path, "w") as f:
                json.dump(lsp_resp, f)
        return lsp_resp
    

    @catch_exception
    def get_symbol_header(self, symbol_name: str) -> str:
        
        declaration = self.get_symbol_info(symbol_name, LSPFunction.Header)
   
        if len(declaration) == 0:
            self.logger.warning(f"No such symbol: {symbol_name} found!")
            return LSPResults.NoResult # No declaration found

        absolute_path = declaration[0]["file_path"]
      
        # read compile_commands.json from project directory
        compile_commands_path = os.path.join(self.cache_dir, self.project_name, "compile_commands.json")
        with open(compile_commands_path, "r") as f:
            compile_commands = json.load(f)
        
        # find all include path
        include_path_set = set()
        for one_cmd in compile_commands:
            
            cmd_dir = one_cmd["directory"]
            for i, args in enumerate(one_cmd["arguments"]):
                if not args.startswith("-I"):
                    continue

                if args == "-I":
                    _include_path = one_cmd["arguments"][i+1]
                else:
                    _include_path = args[2:]
                
                # relative path
                if _include_path.startswith(r"/"):
                    abs_path = _include_path
                else:
                    _path = os.path.join(cmd_dir, _include_path)
                    abs_path = os.path.abspath(_path)
                include_path_set.add(abs_path)

        # find the real path of the header file
        for include_path in include_path_set:
            if absolute_path.startswith(include_path):
                relative_path = os.path.relpath(absolute_path, start=include_path)
                
                self.logger.info(f"Found {symbol_name} in {absolute_path}, header file path: {relative_path}")
                return relative_path
            
        self.logger.warning(f"Error: {symbol_name} found in {absolute_path}, but this path is not include in compile cmd (-I)")
        return ""

    def get_symbol_declaration(self, symbol_name):
        return self.get_symbol_info(symbol_name, LSPFunction.DECLARATION)

    def get_symbol_definition(self, symbol_name):
        return self.get_symbol_info(symbol_name, LSPFunction.DEFINITION)

    def get_symbol_references(self, symbol_name):
        return self.get_symbol_info(symbol_name, LSPFunction.References)



  