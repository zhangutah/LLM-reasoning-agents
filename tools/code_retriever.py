from utils.docker_utils import DockerUtils
from constants import ToolDescMode, LanguageType, LSPFunction
import json
import os
import logging
from constants import LSPResults, Retriever, DockerResults, PROJECT_PATH
from pathlib import Path
from typing import Callable, Any
import functools
import re
from utils.misc import add_lineno_to_code, extract_name

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
        Do not include space in the symbol name.
        :param symbol_name: The symbol name like class name, function name, struct name etc.
        :return: Full path to the header file if found, otherwise None.

        Example:
            get_symbol_header("cJSON") -> "../cJSON.h"
            get_symbol_header("ada::parser::parse_url<ada::url>") -> "ada.h"

        """
    }


def catch_exception(func: Callable[..., list[dict[str, Any]]]) -> Callable[..., list[dict[str, Any]]]:
    @functools.wraps(func)
    def wrapper(self: Any, *args: Any, **kwargs: Any)->list[dict[str, Any]]: 
        try:
            return func(self, *args, **kwargs)
        except Exception as e:
            self.logger.error(f"Error in {func.__name__}: {str(e)}") # type: ignore
            return []
    return wrapper # type: ignore

class CodeRetriever():
    # TODO: how to deal with the same symbol name in different files, we may not know the file path
    '''
    This class is used to retrieve the code information for a project through LSP and language parser.
    It run a python file lsp_code_retriever inside the docker container to get the code information. Therefore, those python files should be copied to the docker container first.
    The python file implementes the LSP client to interact with different LSP servers (clangd for c/c++, ) to get the following information:
    1. Header file path for a symbol name.
    2. Symbol declaration for a symbol name.
    3. Symbol definition for a symbol name.
    4. Symbol cross reference for a symbol name.
    '''

    def __init__(self, oss_fuzz_dir: Path, project_name: str, new_project_name: str, 
                 project_lang: LanguageType, cache_dir: Path, logger: logging.Logger):

        self.oss_fuzz_dir = oss_fuzz_dir
        self.project_name = project_name
        self.new_project_name = new_project_name
        self.project_lang = project_lang
        self.cache_dir = cache_dir
        self.logger = logger
        self.docker_tool = DockerUtils(self.oss_fuzz_dir, self.project_name, self.new_project_name, self.project_lang)

    def view_code(self, file_path: str, lineno: int) -> str:
        """
        View the code around the given line number for file path. This tool will return 20 lines before and after the target line.
        Args:
            file_path (str): The path to the file to read from, like /src/xx/xx.c
            lineno (int): The target line number (0-indexed).
        Returns:
            str: The extracted code as a string.
        """
        lineno += 1  # Convert to 1-indexed for sed command
        context_window = 10  # Number of lines before and after the target line
        # Read the file from the docker container
        start_line =  max(lineno -context_window, 1)
        end_line = lineno + context_window
        read_cmd = f"sed -n '{start_line},{end_line}p' {file_path}"

        result = self.docker_tool.run_cmd(read_cmd)
        # sed 
        if "sed: " in result:
            self.logger.warning(result)
            return f"There is no such file {file_path} in the project."
        
        # add line number to each line
        return add_lineno_to_code(result, start_lineno=start_line - 1)
    
    # def get_all_functions(header_file: str) -> list:
    #     """
    #     """
        
    @catch_exception
    def call_container_code_retriever(self, symbol_name: str, lsp_function: LSPFunction, retriver: Retriever) -> list[dict[str, Any]]:

        compile_out_path = self.oss_fuzz_dir / "build" / "out" / self.new_project_name
        compile_out_path.mkdir(parents=True, exist_ok=True)
        compile_json_path = self.cache_dir / self.project_name / "compile_commands.json"
        workdir = self.docker_tool.run_cmd(["pwd"], timeout=None, volumes=None).strip()
        volumes:dict[str, dict[str, str]] = {str(compile_out_path): {"bind": "/out", "mode": "rw"}, 
                    os.path.join(PROJECT_PATH, "tools"): {"bind": os.path.join(workdir, "tools"), "mode": "ro"}}

        if retriver == Retriever.LSP:
            pyfile = "lsp_code_retriever"
            if self.project_lang in [LanguageType.C, LanguageType.CPP]:
                # the host file must exist for mapping
                if not compile_json_path.exists():
                    self.logger.error(f"Error: {compile_json_path} does not exist")
                    return []
                volumes[str(compile_json_path)] = {"bind": os.path.join(workdir, "compile_commands.json"), "mode": "rw"}

        elif retriver == Retriever.Parser:
            pyfile = "parser_code_retriever"
        else:
            self.logger.error(f"Error: {retriver} is not supported")
            return []
        
        cmd_list:list[str] = ["python", "-m", f"tools.code_tools.{pyfile}",  "--workdir", workdir,  "--lsp-function", lsp_function.value,
                     "--symbol-name", symbol_name, "--lang", self.project_lang.value]
        
        res_str = self.docker_tool.run_cmd(cmd_list, timeout=120, volumes=volumes)
        self.logger.info(f"Calling {retriver}_code_retriever to get {lsp_function} for {symbol_name}")
       
        # docker run error 
        if res_str.startswith(DockerResults.Error.value):
            self.logger.error(f"Error in when calling {retriver}_code_retriever: {res_str}")
            return []

        # check if the response file is generated
        if lsp_function == LSPFunction.StructFunctions:
            file_name = f"{Path(symbol_name).stem}_{lsp_function.value}_{retriver.value}.json"
        else:
            # for other functions, we use the symbol name directly
            file_name = f"{symbol_name}_{lsp_function.value}_{retriver.value}.json"
        save_path = compile_out_path / file_name
        if not save_path.exists():
            self.logger.error(f"Error: {retriver}_code_retriever does not generate the response file: {save_path}")
            return []
        
        # read code retriver response
        with open(save_path, "r") as f:
            res_json = json.load(f)

        msg, lsp_resp = res_json["message"], res_json["response"]

        if msg.startswith(LSPResults.Error.value):
            self.logger.error(f"Error in when calling {retriver}_code_retriever: {msg}")
            return []
        
        if not lsp_resp:
            self.logger.info(f"{retriver}_code_retriever return [], {lsp_function} for {symbol_name}")
            return []
        
        return lsp_resp


    @catch_exception
    def get_symbol_info(self, symbol_name: str, lsp_function: LSPFunction, retriever: Retriever = Retriever.Mixed) -> list[dict[str, Any]]:
        """
        Retrieves the declaration information of a given symbol using the Language Server Protocol (LSP).
        Args:
            symbol_name (str): The name of the symbol for which to retrieve the declaration.
        Returns:
             list[dict]: [{"source_code":"", "file_path":"", "line":""}]
        """

        # Remove the "struct" or "class" prefix from the symbol name
        if symbol_name.startswith("struct") or symbol_name.startswith("class"):
            symbol_name = symbol_name.split(" ")[1]

        if retriever == Retriever.Mixed:
            lsp_resp = self.get_symbol_info_retriever(symbol_name, lsp_function, Retriever.LSP)
            
            if not lsp_resp:
                parser_resp = self.get_symbol_info_retriever(symbol_name, lsp_function, Retriever.Parser)
                return parser_resp
            else:
                return lsp_resp
        else:
            lsp_resp = self.get_symbol_info_retriever(symbol_name, lsp_function, retriever)
            return lsp_resp
    

    @catch_exception
    def get_symbol_info_retriever(self, symbol_name: str, lsp_function: LSPFunction, retriever: Retriever = Retriever.LSP) -> list[dict[str, Any]]:
        """
        Retrieves the declaration information of a given symbol using the Language Server Protocol (LSP).
        Args:
            symbol_name (str): The name of the symbol for which to retrieve the declaration.
        Returns:
             list[dict]: [{"source_code":"", "file_path":"", "line":""}]
        """
        if lsp_function == LSPFunction.StructFunctions:
            save_path = self.cache_dir / self.project_name / f"{Path(symbol_name).stem}_{lsp_function.value}_{retriever.value}.json"
        else:
            save_path = self.cache_dir / self.project_name / f"{symbol_name}_{lsp_function.value}_{retriever.value}.json"
        # get the lsp response from the cache if it exists
        if save_path.exists():
            self.logger.info(f"Getting {lsp_function} for {symbol_name} from cache")
            with open(save_path, "r") as f:
                return json.load(f)
        
        # call the container code retriever
        lsp_resp = self.call_container_code_retriever(symbol_name, lsp_function, retriever)
    
        if self.cache_dir.exists():
            save_path.parent.mkdir(parents=True, exist_ok=True)
            # TODO: same symbol name
            with open(save_path, "w") as f:
                json.dump(lsp_resp, f)

        return lsp_resp


    def get_header_helper(self, symbol_name: str, retriever: Retriever = Retriever.Mixed, 
                                 lsp_function: LSPFunction=LSPFunction.Declaration, forward:bool=False) -> set[str]:
        """
        Find the header file path containing the declaration of a specified symbol name.
        Args:
            symbol_name (str): The name of the symbol to search for like function name, struct name, class name .. .
        Returns:
            str: If the declaration is found, returns the absolute path to the header file.
                 If no declaration is found, returns None.
        Example:
            >>> get_symbol_header("cJSON")
            "/src/cJSON.h"
            >>> get_symbol_header("ada::parser::parse_url<ada::url>")
            "/src/ada-url/ada/url.h"
        """
        all_headers: set[str] = set()
        name_list = symbol_name.split("::")
        for i in range(len(name_list)):
            new_symbol_name = "::".join(name_list[i:])
            declaration = self.get_symbol_info(new_symbol_name, lsp_function, retriever)

            for decl in declaration:
                
                all_headers.add(decl["file_path"].strip())
                # Match typedef struct pattern with variable alias
                pattern = r"typedef[\t ]+(struct|enum|union)[\t ]+(\w+)[\t ]+(\w+)\s*;"
                match = re.search(pattern, decl["source_code"])
                if match and forward:
                    struct_tag = match.group(2)
                    alias_name = match.group(3)
                    if alias_name != symbol_name:
                        continue

                    # get the header file for the forward struct recursively
                    forward_headers = self.get_header_helper(struct_tag, retriever, lsp_function, forward=forward)
                    all_headers.update(forward_headers)
       
        return all_headers


    def get_symbol_header(self, symbol_name: str, retriever: Retriever = Retriever.Mixed) -> str:
        """
        Find the header file path containing the declaration of a specified symbol name.
        Args:
            symbol_name (str): The name of the symbol to search for like function name, struct name, class name .. .
        Returns:
            str: If the declaration is found, returns the absolute path to the header file.
                 If no declaration is found, returns None.
        Example:
            >>> get_symbol_header("cJSON")
            "/src/cJSON.h"
            >>> get_symbol_header("ada::parser::parse_url<ada::url>")
            "/src/ada-url/ada/url.h"
        """

        all_headers: set[str] = set()
        all_headers: set[str] = self.get_header_helper(symbol_name, retriever, LSPFunction.Declaration, forward=True)
        if not all_headers:
            # no need to forward for function definition
            all_headers: set[str] = self.get_header_helper(symbol_name, retriever, LSPFunction.Definition, forward=False)
        
        if all_headers:
        # header must ends with .h
            all_headers = set([header for header in all_headers if header.endswith(".h") or header.endswith(".hpp")])
            return "\n".join(all_headers)
        self.logger.warning(f"No header for symbol {symbol_name} found!")
        return LSPResults.NoResult.value # No declaration found

    def dict_to_str(self, definitions: list[dict[str, Any]], symbol_name:str, lsp_function: LSPFunction) -> str:
        """
        Convert a dictionary to a string representation.
        Args:
            data (dict): The dictionary to convert.
        Returns:
            str: The string representation of the dictionary.
        """

        if len(definitions) > 1:
            self.logger.warning(f"Multiple {lsp_function.value} found for {symbol_name}, please check the symbol name")
        elif len(definitions) == 0:
            self.logger.warning(f"No {lsp_function.value} found for {symbol_name}, please check the symbol name")
            return "No {} found for {}".format(lsp_function.value, symbol_name)
        
        
        # return as a string
        ret_str = ""
        for i, defi in enumerate(definitions):
            if i >= 5:
                self.logger.warning(f"More than 5 {lsp_function.value} found for {symbol_name},Only the first 5 retults are returned")
                break
            ret_str += f"The {i+1}th {lsp_function.value} of {symbol_name} is:\n"
            ret_str += "file_path: {}\n".format(defi["file_path"])

            # limit the source code length to 30 lines
            all_src = defi["source_code"].splitlines()
            limited_src = "\n".join(all_src[:30])  # Limit to first 30 lines
            if defi.get("start_line", 0) != 0:
                ret_str +=  "source_code: \n{}\n".format(add_lineno_to_code(limited_src, defi["start_line"]))
            else:
                ret_str += "source_code: \n{}\n".format(limited_src)

        return ret_str
    
    def get_symbol_declaration(self, symbol_name: str, retriever: Retriever = Retriever.Mixed) -> str:
        """
        Get the declaration of a symbol from the project. The declaration is the signature of the symbol, which does not include the body of the function or class.
        Args:
            symbol_name (str): The name of the symbol to find the declaration for
        Returns:
            str: The declaration of the symbol, or None if not found
                For multiple declarations, returns a combined result
        Example:
            >>> code_retriever.get_symbol_declaration("parse_cJSON")
            file_path: '/src/xx',
            source_code: 'void parse_cJSON(){...}',
        """
        
        declaration = self.get_symbol_info(symbol_name, LSPFunction.Declaration, retriever)
        ret_str = self.dict_to_str(declaration, symbol_name, LSPFunction.Declaration)
        return ret_str

    def get_symbol_definition(self, symbol_name: str, retriever: Retriever = Retriever.Mixed) -> str:
        """
        Get the definition(s) for a specified symbol. The definition includes the full implementation of the symbol, such as a function or class. 
        Args:
            symbol_name (str): The name of the symbol to look up
            retriever (str, optional): The retriever strategy to use. Defaults to Retriever.Mixed.
        Returns:
            str: List of locations where the symbol is defined, or None if not found.

        Example:
            >>> code_retriever.get_symbol_definition("cJSON_Parse")
            file_path: /src/cJSON.c,
            source_code: cJSON *cJSON_Parse(const char *value) {...},
        """

        # limit the code length to 50 lines
        definitions = self.get_symbol_info(symbol_name, LSPFunction.Definition, retriever)
        ret_str = self.dict_to_str(definitions, symbol_name, LSPFunction.Definition)
        return ret_str
    
    def get_symbol_references(self, symbol_name: str, retriever: Retriever = Retriever.Parser) -> list[dict[str, Any]]:
        """
        Get references to a symbol across all workspace files.
        Args:
            symbol_name (str): The name of the symbol to find references for
        Returns:
            list: A list of functions that used the symbol name in the workspace
        Example:
            >>> references = get_symbol_references("cJSON_Parse")
            >>> references
            [{'source_code': 'function1 {...}',
              'file_path': '/src/xx',
              'line': 10},
             {'source_code': 'function2 {...}',
              'file_path': '/src/yy',
              'line': 20}]
        """

        return self.get_symbol_info(symbol_name, LSPFunction.References, retriever)
    

    def get_struct_related_functions(self, symbol_name: str, retriever: Retriever = Retriever.Mixed) -> str:
        """
        This tool is used to find potential functions to initilze and destroy the struct (symbol_name). For complicated struct, 
        it may require special functions to initialize and destroy the struct. It will search the workspace for all functions that are related to the struct name, 
        such as functions that take the struct as an argument or return the struct. 
    
        Args:
            symbol_name (str): The name of the struct to find related functions for
        Returns:
            list: A list of functions that are related to the struct name in the workspace, sorted by the number of references.
        Example:
            >>> related_functions = get_struct_related_functions("cJSON")
            >>> related_functions
            ['function1','function2', ...]
        """
        all_headers: set[str] = self.get_header_helper(symbol_name, retriever, LSPFunction.Declaration, forward=True)
        if not all_headers:
            # no need to forward for function definition
            all_headers: set[str] = self.get_header_helper(symbol_name, retriever, LSPFunction.Definition, forward=False)
        
        if not all_headers:
            self.logger.warning(f"No header for struct {symbol_name} found!")
            return LSPResults.NoResult.value
        
        res_list:list[dict[str, Any]] = []
       
        for header in all_headers:
            res_list += self.get_symbol_info(header, LSPFunction.StructFunctions, Retriever.Parser)
        
        res_list.sort(key=lambda x: x.get("count", 1), reverse=True)
        name_str = ""
        
        count = 1
        for res_json in res_list:
            src = res_json["source_code"]
            
            count += 1
            # extract the function name from the source code
            function_name = extract_name(src)
            name_str += function_name + "\n"
            if count % 10 == 0:
                name_str += "\n"  # Add a newline every 10 functions for readability
            if count >= 50:
                break
            
        return name_str
    
    # def get_functions_from_examples(self, retriever: Retriever = Retriever.Parser) -> str:
    #     """
    #     Get all functions from the examples for the target function. You can call this tool to find potential functions to use in the project.
    #     Args:
    #         retriever (Retriever): The retriever strategy to use. Defaults to Retriever.Parser.
    #     Returns:
    #         str: A string containing all function names separated by commas.
    #     Example:
    #         >>> code_retriever.get_functions_from_examples()
    #         'function1, function2, ...'
    #     """
    #     res_list = self.get_symbol_info("", LSPFunction.Examples, retriever)
        
    #     name_str = ""
    #     for res_json in res_list:
    #         src = res_json["source_code"]
    #         # extract the function name from the source code
    #         function_name = extract_name(src)
    #         name_str += function_name+", "
    #     return name_str