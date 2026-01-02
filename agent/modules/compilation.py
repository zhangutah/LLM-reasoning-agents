from itertools import count
import stat
from constants import LanguageType, CompileResults, ValResult, LSPFunction
import re
import logging
from typing import Any, Optional
from agent_tools.fuzz_tools.log_parser import CompileErrorExtractor, FuzzLogParser
from utils.misc import save_code_to_file, extract_name
from agent_tools.fuzz_tools.compiler import Compiler
from agent_tools.code_retriever import CodeRetriever
from pathlib import Path
from langgraph.graph import END  # type: ignore


def find_base_dir(abs_path: str, rel_path: str) -> Optional[str]:
    abs_parts = Path(abs_path).resolve().parts
    rel_parts = Path(rel_path).parts

    for i in range(len(abs_parts) - len(rel_parts) + 1):
        if abs_parts[i:i+len(rel_parts)] == rel_parts:
            return str(Path(*abs_parts[:i])) + "/"
    return None


class CompilerWraper(Compiler):
    def __init__(self, oss_fuzz_dir: Path, benchmark_dir: Path, project_name: str, new_project_name: str, code_retriever: CodeRetriever,
                     project_lang: LanguageType, harness_dict:dict[str, Path], compile_enhance: bool,
                     save_dir: Path, cache_dir: Path, logger: logging.Logger, function_signature: str = ""):
        super().__init__(oss_fuzz_dir, benchmark_dir, project_name, new_project_name)
        self.logger = logger
        self.project_lang = project_lang
        self.code_retriever = code_retriever
        self.save_dir = save_dir
        self.cache_dir = cache_dir
        self.harness_dict = harness_dict
        self.start_index = 0
        self.compile_enhance = compile_enhance
        self.function_signature = function_signature

    def extract_error_msg(self, all_msg: str) -> str:
        '''
        Extract the error message from the raw message. 
        If you wanna customize the error message extraction, you can override this function.
        '''
        all_errors = CompileErrorExtractor(self.project_lang).extract_error_message(all_msg)
        if len(all_errors) != 0:
            return "\n".join(all_errors)
        
        # may have fuzzer error
        # Define the error patterns
        error_patterns = ['ERROR: LeakSanitizer',  'ERROR: libFuzzer:', 'ERROR: AddressSanitizer']
        if any(error_pattern in all_msg for error_pattern in error_patterns):
            fuzz_res, error_type_line, stack_list = FuzzLogParser(self.project_lang).parse_str(all_msg)
            if fuzz_res == ValResult.Crash:
                error_type = error_type_line[0] if len(error_type_line) > 0 else "Unknown Crash, Unable to extract the error message"
                first_stack = stack_list[0] if len(stack_list) > 0 else ["Unknown Crash, Unable to extract the stack trace"]
                fuzz_error_msg = error_type + "\n" + "\n".join(first_stack)
                return fuzz_error_msg
       
        # return the last 100 lines
        return all_msg[-100:]
    

    # TODO change this
    def _match_link_pattern(self, harness_file:Path, pattern:str,  error_msg: str) -> bool:
           # Find all matches
        matches = re.findall(pattern, error_msg)
        harness_file_name = harness_file.name
        local_harness_path = self.oss_fuzz_dir / "projects" / self.new_project_name / harness_file.name 
        harness_code = local_harness_path.read_text()
        # Print the results
        for file_name, function in matches:
            # print(f"Error in file {file_name} for function {function_name}")
            if file_name.strip() != harness_file_name.strip():
                return True
            else:
                # only for undefined reference
                all_headers = list(self.code_retriever.get_header_helper(function, forward=True))
                all_headers = set([header for header in all_headers if header.endswith(".h") or header.endswith(".hpp")])
                if all_headers and all(h.strip() in harness_code for h in all_headers):
                    return True
        return False

    def is_header_link_error(self, error_msg: str, harness_path: Path) -> bool:
             # "egif_target.cc:28:10: fatal error: '/src/genfiles/gif_fuzz_proto.pb.h' file not found"
        pattern = re.compile(
                    r""".*\.(?:c|cpp|cc|cxx).*fatal error:\s*['"]([^'"]+)['"]\sfile\snot\sfound""",
                    re.IGNORECASE,
                )
        matches = pattern.findall(error_msg)
        if not matches:
            return False
        
        # for each missing header, check if it is related to the harness file
        local_harness_path = self.oss_fuzz_dir / "projects" / self.new_project_name / harness_path.name 
        harness_code = local_harness_path.read_text()
        for header in matches:
            # check if the header is included in the harness code
            #  and can be located by the code retriever (this is not strictly matched, but a heuristic)
            if header in harness_code and self.code_retriever.get_file_location_tool(header):
                return True
        return False

    def is_link_error(self, error_msg: str, harness_path: Path) -> bool:
        '''Check if the error message is a link error'''

        # harness_file_name = harness_path.name
        if self.is_header_link_error(error_msg, harness_path):
            return True

        link_error_pattern = r"DWARF error: invalid or unhandled FORM value: 0x25"
        if link_error_pattern not in error_msg:
            return False
        
        # Regular expression to match the errored file and undefined function
        # TODO only test on C Projects
        undefined_pattern = r"([\w\-]+\.(?:c|o|cc|cpp)):.*undefined reference to `([^']+)'"
        multi_definiation_pattern = r"([\w\-]+\.(?:c|o|cc|cpp)):.*multiple definition of `([^']+)'"
        # Find all matches
        for pattern in [undefined_pattern, multi_definiation_pattern]:
            if self._match_link_pattern(harness_path, pattern, error_msg):
                return True
     
        return False

    def get_headers_from_error(self, error_msg: str) -> list[str]:
        pattern = re.compile(
                    r""".*\.(?:h|hpp|hxx).*fatal error:\s*['"]([^'"]+)['"]\sfile\snot\sfound""",
                    re.IGNORECASE,
                )
        matches = pattern.findall(error_msg)
        return matches

    def is_include_error(self, error_msg: str) -> bool:
        '''Check if the error message is an include error'''
        matches = self.get_headers_from_error(error_msg)
        if matches:
            return True
        return False


    def handle_include_error(self, error_msg: str) -> None:
        '''Handle the include error'''

        headers = self.get_headers_from_error(error_msg=error_msg)

        include_path: list[str] = []
        for header in headers:
            abs_files = self.code_retriever.get_file_location_tool(header)
            for abs_file in abs_files:
                base_dir = find_base_dir(abs_file, header)
                if base_dir:
                    include_path.append(base_dir)

        # rewrite build.sh
        self.include_path.update(include_path)

        if self.include_path:
            include_file = self.save_dir / "include_path.txt"
            save_code_to_file("\n".join(self.include_path), include_file)

        # return False
    def is_missing_header_error(self, error_msg: str) -> bool:
        '''Check if the error message is a missing header error'''
        pattern = re.compile(
            r""".*\.(?:h|hpp|hxx).*error:.*""",
            re.IGNORECASE,
        )
        match = pattern.search(error_msg)
        if match:
            return True
        return False

    def _handle_static_function(self) -> Optional[str]:
        if not self.function_signature:
            return None
            
        func_name = extract_name(self.function_signature, keep_namespace=True, language=self.project_lang)
        
        # get definition
        defs = self.code_retriever.get_symbol_info(func_name, LSPFunction.Definition)
        if not defs:
            return None
        
        target_def = defs[0]
        source_code = target_def.get("source_code", "")
        file_path = target_def.get("file_path", "")
        
      
            
        # It is static.
        self.logger.info(f"Detected static function {func_name} in {file_path}. Removing static keyword.")
        
        # Read the full file content
        full_content = self.code_retriever.docker_tool.exec_in_container(self.code_retriever.container_id, f"cat {file_path}")
        
        if "No such file or directory" in full_content:
             self.logger.error(f"Failed to read file {file_path}")
             return None

        lines = full_content.splitlines()

        length = len(lines)
        counts = 0
        static_flag = False
        # Find and modify the line containing the function definition
        for i, line in enumerate(lines[::-1]):
            
            # same line
            if f"{func_name}(" in line and  "static" in line:
                # Remove the 'static' keyword
                modified_line = line.replace("static", "  ")
                lines[length-i-1] = modified_line
                self.logger.info(f"Modified line {length-i-1} in {file_path}: {modified_line}")
                counts += 1
                if counts == 2:
                    break  
                continue

            if f"{func_name}(" in line:
                static_flag = True
                continue

            # not a declaration line or definition line, no need to continue
            if ";" in line:
                static_flag = False

            # next lines
            if static_flag and "static" in line:
                # check previous lines until finding static or hitting a non-declaration line 
                modified_line = line.replace("static", "  ")
                lines[length-i-1] = modified_line
                self.logger.info(f"Modified line {length-i-1} in {file_path}: {modified_line}")
                counts += 1
            
                # Assuming only two occurrence needs to be modified
                if counts == 2:
                    break  
        
        modified_content = "\n".join(lines)
        
        # Save modified file
        modified_filename = Path(file_path).name
        local_modified_path = self.oss_fuzz_dir / "projects" / self.new_project_name / modified_filename
        save_code_to_file(modified_content, local_modified_path)
        
        return f"COPY {modified_filename} {file_path}"

    def compile_helper(self, harness_code: str, harness_path: Path, fuzzer_name: str, fix_counter: int) -> dict[str, Any]: # type: ignore

        # save the harness code to current output directory
        save_code_to_file(harness_code, self.save_dir / "harness.txt")
        
        # compile the harness file
        self.logger.info(f'Compile Start for draft_fix{fix_counter} using {fuzzer_name}.')
        
        cmd = self._handle_static_function()
        compile_res, all_msg = self.compile_harness(harness_code, harness_path, fuzzer_name, cmd=cmd)
        self.logger.info(f'Compile End for draft_fix{fix_counter} using {fuzzer_name}. Res: {compile_res}')

        save_code_to_file(all_msg, self.save_dir / f"build_{fix_counter}.log")

        # Project realted error, No need to continue
        if compile_res in [CompileResults.ImageError, CompileResults.FuzzerError]:
            return {"messages": ("user", END + compile_res.value)}
        
        elif compile_res == CompileResults.Success:
            return {"messages": ("user", CompileResults.Success.value), "build_msg": all_msg, "fuzzer_name": fuzzer_name, "fuzzer_path": harness_path}

        # compile error
        elif compile_res == CompileResults.CodeError:
            # extract error msg
            error_msg = self.extract_error_msg(all_msg)
            save_code_to_file(error_msg, self.save_dir / f"build_error_{fix_counter}.log")

            # no compile enhance, return directly
            if not self.compile_enhance:
                # save raw error message
                return {"messages": ("user", compile_res.value), "build_msg": error_msg, "fuzzer_name": fuzzer_name, "fuzzer_path": harness_path}

            # try to enhance the compile error
            error_type = CompileResults.CodeError
            if self.is_link_error(error_msg, harness_path):
                error_type = CompileResults.LinkError
            # check include error first since this error can also be detected as  missing headers
            elif self.is_include_error(error_msg):
                error_type = CompileResults.IncludeError
            elif self.is_missing_header_error(error_msg):
                error_type = CompileResults.MissingHeaderError

            self.logger.info(f"Compile End, Res: {error_type} for draft_fix{fix_counter} using {fuzzer_name}.")
            # save raw error message
            return {"messages": ("user", error_type.value), "build_msg": error_msg, "fuzzer_name": fuzzer_name, "fuzzer_path": harness_path}
            

    def compile(self, state: dict[str, Any]) -> dict[str, Any]: # type: ignore
        '''Compile the harness file'''
        
        harness_path = state.get("fuzzer_path", list(self.harness_dict.values())[0])
        fuzzer_name = state.get("fuzzer_name", list(self.harness_dict.keys())[0])
        fix_counter = state.get("fix_counter", 0)
        msg = self.compile_helper(state["harness_code"], harness_path, fuzzer_name, fix_counter)

        if msg["messages"][1] not in [CompileResults.LinkError.value, 
                                          CompileResults.IncludeError.value,
                                          CompileResults.MissingHeaderError.value,
                                          CompileResults.FuzzerError.value]:
            return msg
        
        # only one harness file, return directly
        if msg["messages"][1] == CompileResults.FuzzerError.value and len(self.harness_dict) == 1:
            return msg
        
        # try other harness files
        for i, (_fuzzer_name, _harness_path) in enumerate(self.harness_dict.items()):
            
            # skip the current one
            if _fuzzer_name == fuzzer_name:
                continue
            if i < self.start_index:
                continue
            msg = self.compile_helper(state["harness_code"], _harness_path, _fuzzer_name, fix_counter)
            self.start_index = i+1
            
            if msg["messages"][1]  not in [CompileResults.LinkError.value, 
                                        CompileResults.IncludeError.value,
                                        CompileResults.MissingHeaderError.value,
                                        CompileResults.FuzzerError.value]:
                return msg
            
        # tried all harness files, still have error
        self.start_index = 0 # reset for next time, it will try all harness again
        if msg["messages"][1] == CompileResults.FuzzerError.value:
            # recompile with the first harness
            fuzzer_name, harness_path = list(self.harness_dict.items())[0]
            msg = self.compile_helper(state["harness_code"], harness_path, fuzzer_name, fix_counter)
            return msg

        # handle link error
        if msg["messages"][1] == CompileResults.LinkError.value:
            return msg
       
        # handle include error
        if msg["messages"][1] == CompileResults.IncludeError.value:
            for i in range(3):
                self.handle_include_error(msg["build_msg"]) # type: ignore
                msg = self.compile_helper(state["harness_code"], harness_path, fuzzer_name, fix_counter)
                if msg["messages"][1] != CompileResults.IncludeError.value:
                    break
            # after 3 tries, return directly
            return msg

        # handle missing header error
        if msg["messages"][1] == CompileResults.MissingHeaderError.value:
            driver_list = self.code_retriever.get_all_driver_examples()

            build_msg = msg["build_msg"] + "\n\n\n// Here are some driver examples that might help, especially the headers:\n"
            for driver in driver_list:
                if driver[0] == harness_path.name:
                    driver_lines = driver[1].splitlines()[:200]
                    build_msg +="\n".join(driver_lines)
                # else:
                    # build_msg += f"//   {driver}\n"
            # provide suggestion let the LLM handle it
            return {"messages": ("user", CompileResults.MissingHeaderError.value), "build_msg": build_msg, "fuzzer_name": fuzzer_name, "fuzzer_path": harness_path}
     