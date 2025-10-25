from constants import LanguageType, CompileResults
import re
import logging
from typing import Any
from agent_tools.fuzz_tools.log_parser import CompileErrorExtractor
from utils.misc import save_code_to_file
from agent_tools.fuzz_tools.compiler import Compiler
from agent_tools.code_retriever import CodeRetriever
from pathlib import Path
from langgraph.graph import END  # type: ignore

class CompilerWraper(Compiler):
    def __init__(self, oss_fuzz_dir: Path, benchmark_dir: Path, project_name: str, new_project_name: str, code_retriever: CodeRetriever,
                     project_lang: LanguageType, harness_dict:dict[str, Path], 
                     save_dir: Path, cache_dir: Path, logger: logging.Logger):
        super().__init__(oss_fuzz_dir, benchmark_dir, project_name, new_project_name)
        self.logger = logger
        self.project_lang = project_lang
        self.code_retriever = code_retriever
        self.save_dir = save_dir
        self.cache_dir = cache_dir
        self.harness_dict = harness_dict
        self.start_index = 0

    def extract_error_msg(self, all_msg: str) -> str:
        '''
        Extract the error message from the raw message. 
        If you wanna customize the error message extraction, you can override this function.
        '''
        all_errors = CompileErrorExtractor(self.project_lang).extract_error_message(all_msg)
        return "\n".join(all_errors)

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

    def is_link_error(self, error_msg: str, harness_path: Path) -> bool:
        '''Check if the error message is a link error'''

        # harness_file_name = harness_path.name
        # 
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

    def compile(self, state: dict[str, Any]) -> dict[str, Any]: # type: ignore
        '''Compile the harness file'''
        
        fix_count = state.get("fix_counter", 0)

        # save the harness code to current output directory
        save_code_to_file(state["harness_code"], self.save_dir / "harness.txt")
      
        # if self.counter > self.max_compile:
            # log_if_exists(self.logger, f"Max compile times reached for {self.new_project_name}:{self.project_harness_name}", logger_level=logging.INFO)
            # return {"messages": END}

        for i, (fuzzer_name, harness_path) in enumerate(self.harness_dict.items()):
            
            if i < self.start_index:
                continue

            self.logger.info(f'Compile Start for draft_fix{fix_count} using {fuzzer_name}.')
            # compile the harness file
            compile_res, all_msg = super().compile(state["harness_code"], harness_path, fuzzer_name)

            self.logger.info(f'Compile End for draft_fix{fix_count} using {fuzzer_name}. Res: {compile_res}')

            save_code_to_file(all_msg, self.save_dir / f"build_{fix_count}.log")

            # Project realted error, No need to continue
            if compile_res in [CompileResults.ImageError, CompileResults.FuzzerError]:
                return {"messages": ("user", END + compile_res.value)}
            
            # compile error
            elif compile_res == CompileResults.CodeError:
                # extract error msg
                error_msg = self.extract_error_msg(all_msg)
                save_code_to_file(error_msg, self.save_dir / f"build_error_{fix_count}.log")

                if not self.is_link_error(error_msg, harness_path):
                    # save raw error message
                    return {"messages": ("user", compile_res.value), "build_msg": error_msg, "fuzzer_name": fuzzer_name, "fuzzer_path": harness_path}
                else:
                    # link error, try next harness
                    self.logger.error(f"Link Error for draft_fix{fix_count} using {fuzzer_name}, Now try another harness file.")
                    self.start_index = i+1

            # compile success
            else:
                return {"messages": ("user", CompileResults.Success.value), "build_msg": all_msg, "fuzzer_name": fuzzer_name, "fuzzer_path": harness_path}

        # tried all harness, Unable to fix, let the LLM try 
        self.start_index = 0 # reset for next time, it will try all harness again
        fuzzer_name, harness_path = list(self.harness_dict.items())[0]      
        compile_res, all_msg = super().compile(state["harness_code"], harness_path, fuzzer_name)
        error_msg = self.extract_error_msg(all_msg)
        save_code_to_file(error_msg, self.save_dir / f"build_error_{fix_count}.log")
        save_code_to_file(all_msg, self.save_dir / f"build_{fix_count}.log")
        # save raw error message
        return {"messages": ("user", CompileResults.CodeError.value), "build_msg": error_msg, "fuzzer_name": fuzzer_name, "fuzzer_path": harness_path}


