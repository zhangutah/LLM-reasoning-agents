from utils.docker_utils import DockerUtils
from constants import LanguageType, LSPFunction
import json
import logging
from constants import LSPResults, Retriever, DockerResults
from pathlib import Path
from typing import Callable, Any
import functools
import re
from utils.misc import add_lineno_to_code, filter_examples
import time

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
                 project_lang: LanguageType, usage_token_limit: int, cache_dir: Path, logger: logging.Logger):

        self.oss_fuzz_dir = oss_fuzz_dir
        self.project_name = project_name
        self.new_project_name = new_project_name
        self.project_lang = project_lang
        self.usage_token_limit = usage_token_limit
        self.cache_dir = cache_dir
        self.logger = logger
        self.docker_tool = DockerUtils(self.oss_fuzz_dir, self.project_name, self.new_project_name, self.project_lang)
        # Start and keep the container running
        for _ in range(3):
            self.container_id = self.docker_tool.start_container(timeout=120)  # type: ignore
            if not self.container_id.startswith(DockerResults.Error.value):
                break

        assert not self.container_id.startswith(DockerResults.Error.value), f"Failed to start container: {self.container_id}"
        # run 
        res = self.docker_tool.exec_in_container(self.container_id, ["bear compile"], timeout=1200)
        self.logger.info(f"bear res: {res.splitlines()[-2:]}")
        if res.startswith(DockerResults.Error.value):
            self.remove_container()
            self.logger.error(f"Failed to run bear compile: {res}")
            raise Exception(f"Failed to run bear compile: {res}")

    def remove_container(self):
        # Ensure the container is stopped when the object is deleted
        try:
            if hasattr(self, "container_id") and self.container_id:
                self.docker_tool.remove_container(self.container_id)
        except Exception as e:
            self.logger.error(f"Error stopping container: {e}")

    def view_code(self, file_path: str, lineno: int, context_window: int=100, num_flag: bool=True) -> str:
        """
        View the code around the given line number for file path. This tool will return 20 lines before and after the target line.
        Args:
            file_path (str): The path to the file to read from, like /src/xx/xx.c
            lineno (int): The target line number (0-indexed).
        Returns:
            str: The extracted code as a string.
        """
        lineno += 1  # Convert to 1-indexed for sed command
        # Read the file from the docker container
        start_line =  max(lineno - context_window, 1)
        end_line = lineno + context_window
        read_cmd = f"sed -n '{start_line},{end_line}p' {file_path}"

        # Use exec_in_container instead of run_cmd
        result = self.docker_tool.exec_in_container(self.container_id, read_cmd)
        if "sed: " in result:
            self.logger.warning(result)
            return f"There is no such file {file_path} in the project."
        if num_flag:
            # add line number to each line
            return add_lineno_to_code(result, start_lineno=start_line - 1)
        return result


    @catch_exception
    def call_container_code_retriever(self, symbol_name: str, lsp_function: LSPFunction, retriver: Retriever) -> list[dict[str, Any]]:

        compile_out_path = self.oss_fuzz_dir / "build" / "out" / self.new_project_name
        compile_out_path.mkdir(parents=True, exist_ok=True)
        workdir = self.docker_tool.run_cmd(["pwd"], volumes=None).strip()
        if retriver == Retriever.LSP:
            pyfile = "lsp_code_retriever"
        elif retriver == Retriever.Parser:
            pyfile = "parser_code_retriever"
        else:
            self.logger.error(f"Error: {retriver} is not supported")
            return []

        cmd_list = ["python", "-m", f"agent_tools.code_tools.{pyfile}", "--workdir", workdir, "--lsp-function", lsp_function.value,
                    "--symbol-name", symbol_name, "--lang", self.project_lang.value]

        # Use exec_in_container instead of run_cmd
        if lsp_function == LSPFunction.AllSymbols:
            timeout = 300  # Increase timeout for all symbols:
        else:
            timeout = 120  # Default timeout for other functions

        res_str = self.docker_tool.exec_in_container(self.container_id, cmd_list, timeout=timeout)
        self.logger.info(f"Calling {retriver}_code_retriever to get {lsp_function} for {symbol_name}")

        if res_str.startswith(DockerResults.Error.value):
            self.logger.error(f"Error in when calling {retriver}_code_retriever: {res_str}")
            return []

        if lsp_function == LSPFunction.StructFunctions:
            file_name = f"{Path(symbol_name).stem}_{lsp_function.value}_{retriver.value}.json"
        else:
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

        if "<" in symbol_name:
            # If the symbol name contains template, we only use the part before the template
            symbol_name = symbol_name.split("<")[0]

        if retriever == Retriever.Mixed:
            start = time.time()
            resp = self.get_symbol_info_retriever(symbol_name, lsp_function, Retriever.LSP)
            print(f"get_symbol_info_retriever for {symbol_name} took {time.time() - start:.2f} seconds")
            if not resp:
                resp = self.get_symbol_info_retriever(symbol_name, lsp_function, Retriever.Parser)
        else:
            resp = self.get_symbol_info_retriever(symbol_name, lsp_function, retriever)
        
        # deduplicate the response based on source_code
        unique_sources: set[str] = set()
        deduped_resp: list[dict[str, Any]] = []
        for item in resp:
            source_code = item.get("source_code", "")
            if source_code not in unique_sources:
                unique_sources.add(source_code)
                deduped_resp.append(item)
        self.logger.info(f"Found {len(resp)} {lsp_function.value} for {symbol_name} with {retriever.value} retriever")
        return deduped_resp  # No declaration found

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
        Find the header file path containing the declaration of a specified symbol name. If the symbol has namespace, you must include it.  
        For example, passing "ada::parser::parse_url<ada::url>" instead of "parse_url" as the symbol name. 
        This function will search for the header file that contains the declaration of the symbol name.
        Args:
            symbol_name (str): The name of the symbol to search for like function name, struct name, class name .. .
        Returns:
            str: If the declaration is found, returns the absolute path to the header file.
                 If no declaration is found, returns None.
        Example:
            >>> get_symbol_header("cJSON")
            "/src/cJSON.h"
            >>> get_symbol_header("parse_url")
            "/src/ada/include/ada/parser.h"
        """
        all_headers: set[str] = set()

        declaration = self.get_symbol_info(symbol_name, lsp_function, retriever)

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
                
                # typedef struct Log Log; This will cause infinite loop
                if struct_tag == alias_name:
                    continue

                # get the header file for the forward struct recursively
                forward_headers = self.get_header_helper(struct_tag, retriever, lsp_function, forward=forward)
                all_headers.update(forward_headers)
       
        return all_headers


    def get_symbol_header(self, symbol_name: str, retriever: Retriever = Retriever.Mixed) -> str:
        """
        Find the header file path containing the declaration of a specified symbol name. If the symbol has namespace, you must include it.  
        For example, passing "ada::parser::parse_url<ada::url>" instead of "parse_url" as the symbol name. 
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
        # First check if it's a standard library symbol
        stdlib_header = self.get_stdlib_header(symbol_name)
        if stdlib_header:
            return stdlib_header

        # Continue with regular lookup process for non-standard library symbols
        all_headers: set[str] = set()
        all_headers: set[str] = self.get_header_helper(symbol_name, Retriever.LSP, LSPFunction.Declaration, forward=True)
        if not all_headers:
            all_headers: set[str] = self.get_header_helper(symbol_name, Retriever.Parser, LSPFunction.Declaration, forward=True)
        # no need to forward for function definition
        if not all_headers:
            all_headers: set[str] = self.get_header_helper(symbol_name, Retriever.LSP, LSPFunction.Definition, forward=False)
        if not all_headers:
            all_headers: set[str] = self.get_header_helper(symbol_name, Retriever.Parser, LSPFunction.Definition, forward=False)
        if not all_headers:
            self.logger.warning(f"No header for symbol {symbol_name} found!")
            return LSPResults.NoResult.value + f". No header for symbol {symbol_name} found!" # No header found
        else:
            # header must ends with .h or .hpp
            all_headers = set([header for header in all_headers if header.endswith(".h") or header.endswith(".hpp")])
            return "\n".join(all_headers)

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
            ret_str += "line: {}\n".format(defi["line"])
            # limit the source code length to 30 lines
            all_src = defi["source_code"].splitlines()
            limited_src = "\n".join(all_src[:30])  # Limit to first 30 lines
            if defi.get("start_line", 0) != 0:
                ret_str +=  "source_code: \n{}\n".format(add_lineno_to_code(limited_src, defi["start_line"]))
            else:
                ret_str += "source_code: \n{}\n".format(limited_src)

        return ret_str
    
    # def deduplicate_res(self, resp:list[dict[str, Any]]):

    #     de_resp: list[dict[str, Any]] = []
    #     for res in resp:
    #         if res["source_code"] == "":



    def get_symbol_declaration(self, symbol_name: str, retriever: Retriever = Retriever.Mixed) -> str:
        """
        Get the declaration of a symbol from the project. The declaration is the signature of the symbol, which does not include the body of the function or class.
        If the symbol has namespace, you must include it. For example, passing "ada::parser::parse_url<ada::url>" instead of "parse_url" as the symbol name. 
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
        If the symbol has namespace, you must include it. For example, passing "ada::parser::parse_url<ada::url>" instead of "parse_url" as the symbol name. 
        Args:
            symbol_name (str): The name of the symbol to look up
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
    
    def get_all_symbol_references(self, symbol_name: str, retriever: Retriever = Retriever.Mixed) -> list[dict[str, Any]]:
        return  self.get_symbol_info(symbol_name, LSPFunction.References, retriever)
        
    def get_symbol_references(self, symbol_name: str, retriever: Retriever = Retriever.Parser) -> str:
        """
        Get references to a symbol across all workspace files.
        Args:
            symbol_name (str): The name of the symbol to find references for
        Returns:
            str: A string containing the source code of the references
        Example:
            >>> references = get_symbol_references("cJSON_Parse")
            >>> references
            "// cJSON *cJSON_Parse(const char *value) {...} // in cJSON.c\ncJSON_Parse(...); // in main.c\n..."
        """
        # if "::" in symbol_name:
        #     # if the symbol name contains namespace, we need to use the full name
        #     symbol_name = symbol_name.split("::")[-1]

        ref_list = self.get_symbol_info(symbol_name, LSPFunction.References, retriever)
        
        example = filter_examples(ref_list, self.project_lang, self.usage_token_limit)
    

        # comment the example
        example_msg = f"\n // the usage of {symbol_name} is as follows: \n"
        for line in example.splitlines():
            # add comment
            example_msg += "// " + line + "\n"

        return example_msg


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
            count += 1
            # extract the function name from the source code
            name_str += res_json["function_name"] + "\n"
            if count % 10 == 0:
                name_str += "\n"  # Add a newline every 10 functions for readability
            if count >= 50:
                break
            
        return name_str

    def get_all_functions(self) -> list[dict[str, Any]]:
        return self.get_symbol_info("All", LSPFunction.AllSymbols, Retriever.Mixed)

    def get_all_headers(self) -> list[str]:
        """
        Get all header files in the project.
        Returns:
            list[str]: A list of header file paths.
        """
        headers = self.get_symbol_info("All", LSPFunction.AllHeaders, Retriever.Mixed)
        headers = [h for h in headers if h["file_path"].endswith((".h", ".hpp", ".hh", ".hxx"))]
        return [header["file_path"] for header in headers if "file_path" in header]
   
    def get_stdlib_header(self, symbol_name: str) -> str:
        """
        Maps standard library symbols to their corresponding header files.
        
        Args:
            symbol_name (str): The name of the symbol to look up in standard libraries
            
        Returns:
            str: The header file for the standard library symbol, or empty string if not found
        """
        # Handle primitive C/C++ types that don't need a header
        primitive_types = {"int", "char", "float", "double", "void", "long", "short", "signed", "unsigned"}
        if symbol_name in primitive_types:
            self.logger.info(f"Symbol {symbol_name} is a primitive type and doesn't require a header")
            return f"// {symbol_name} is a primitive type and doesn't require a header"
        
        # Standard library symbols mapping
        std_lib_symbols = {
            # C standard library
            "bool": "stdbool.h",
            "size_t": "stddef.h",
            "NULL": "stddef.h",
            "int8_t": "stdint.h", 
            "uint8_t": "stdint.h",
            "int16_t": "stdint.h",
            "uint16_t": "stdint.h",
            "int32_t": "stdint.h",
            "uint32_t": "stdint.h",
            "int64_t": "stdint.h",
            "uint64_t": "stdint.h",
            "malloc": "stdlib.h",
            "free": "stdlib.h",
            "printf": "stdio.h",
            "scanf": "stdio.h",
            "FILE": "stdio.h",
            "fopen": "stdio.h",
            "memcpy": "string.h",
            "strcpy": "string.h",
            "strlen": "string.h",
            "time_t": "time.h",
            "errno": "errno.h",
            
            # C++ standard library (std:: namespace is handled separately)
            "std::string": "string",
            "std::vector": "vector",
            "std::map": "map",
            "std::set": "set",
            "std::unordered_map": "unordered_map",
            "std::cout": "iostream",
            "std::cin": "iostream",
            "std::cerr": "iostream",
            "std::unique_ptr": "memory",
            "std::shared_ptr": "memory",
            "std::weak_ptr": "memory",
            "std::thread": "thread",
            "std::mutex": "mutex",
            "std::exception": "exception",
            "std::regex": "regex",
            "std::function": "functional"
        }
        
        # Check if symbol is in standard library lookup table
        if symbol_name in std_lib_symbols:
            header = std_lib_symbols[symbol_name]
            self.logger.info(f"Symbol {symbol_name} is from standard library, header: {header}")
            return header
        
        # Handle std:: namespace prefix
        if symbol_name.startswith("std::"):
            # Extract the part after std::
            base_name = symbol_name[5:]
            # Check if base name is in the lookup table
            if base_name in std_lib_symbols:
                header = std_lib_symbols[base_name]
                self.logger.info(f"Symbol {symbol_name} is from standard library, header: {header}")
                return header
        
        # Return empty string if the symbol is not found in standard library
        return ""
    
    def get_symbol_header_tool(self, symbol_name: str) -> str:
        return self.get_symbol_header(symbol_name, Retriever.Mixed)
    def get_symbol_declaration_tool(self, symbol_name: str) -> str:
        return self.get_symbol_declaration(symbol_name, Retriever.Mixed)
    def get_symbol_definition_tool(self, symbol_name: str) -> str:
        return self.get_symbol_definition(symbol_name, Retriever.Mixed)
    def get_symbol_references_tool(self, symbol_name: str) -> str:
        return self.get_symbol_references(symbol_name, Retriever.Parser)
    def get_struct_related_functions_tool(self, symbol_name: str) -> str:
        return self.get_struct_related_functions(symbol_name, Retriever.Mixed)
  