from harness_agent.fixing.raw import FixerPromptBuilder

from constants import LanguageType, Retriever
from agent_tools.code_retriever import CodeRetriever
import logging
from typing import Optional
from bench_cfg import BenchConfig
from ossfuzz_gen import benchmark as benchmarklib
from utils.misc import add_lineno_to_code

class ISSTAFixerPromptBuilder(FixerPromptBuilder):
    def __init__(self, benchcfg: BenchConfig, oss_fuzz_benchmark: Optional[benchmarklib.Benchmark], project_name: str, new_project_name: str, code_retriever: CodeRetriever,
                 logger: logging.Logger, compile_fix_prompt: str, fuzz_fix_prompt: str,  project_lang: LanguageType):
        super().__init__(benchcfg, oss_fuzz_benchmark, project_name, new_project_name, code_retriever, logger, compile_fix_prompt, fuzz_fix_prompt, project_lang)


    def build_compile_prompt(self, harness_code: str, error_msg: str, fuzzer_path: str)-> str:
        '''
        Build the prompt for the code fixer. If you need to customize the prompt, you can override this function.
        '''
        error_msg = self.reduce_msg(error_msg)

        # find the function name in the error message
        func_dict = self.code_retriever.get_all_functions()
        func_name, func_sig = None, None
        for func in func_dict:
            if func['name'] in error_msg:
                func_name = func['name']
                func_sig = func['signature']
                break

        if not func_name or not func_sig:
            self.logger.info(f"Function name not found in error message")
            return self.compile_fix_prompt.format(harness_code=add_lineno_to_code(harness_code, 1), error_msg=error_msg, project_lang=self.project_lang, fuzzer_path=fuzzer_path)

        comment_example = self.code_retriever.get_symbol_references(func_name, Retriever.Parser)
    
        # declaration
        error_msg += f"\n // the declaration of {func_name} is as follows: \n"
        for line in func_sig.splitlines():
            # add comment
            error_msg += "// " + line + "\n"

        # comment the example
        error_msg += comment_example

        return self.compile_fix_prompt.format(harness_code=add_lineno_to_code(harness_code, 1), error_msg=error_msg, project_lang=self.project_lang, fuzzer_path=fuzzer_path)

    def build_fuzz_prompt(self, harness_code: str, error_msg: str, fuzzer_path: str)-> str:
        '''
        Build the prompt for the code fixer. If you need to customize the prompt, you can override this function.
        '''
        # extract 
        #1 0x5639df576b54 in ixmlDocument_createAttributeEx /src/pupnp/ixml/src/document.c:269:26
        reversed_stack = error_msg.split("\n")[::-1]
        index = None
        for i, line in enumerate(reversed_stack):
            if not line.strip().startswith("#"):
                continue
            # find the first api of the project in stack trace
            if "LLVMFuzzerTestOneInput" in line:
                index = i
                break
        
        if index and index+1 < len(reversed_stack):
            crash_line = reversed_stack[index+1]

            row_data = crash_line.strip().split(" ")

            # 5 for C
            if len(row_data) != 5:
                self.logger.info(f"Error message format is not correct: {crash_line}")
            else:
                _, _, _, func_name, _ = row_data
                
                comment_example = self.code_retriever.get_symbol_references(func_name, Retriever.Parser)
            
                error_msg += "\n\nThe crash line is: " + crash_line + "\n"
                # TODO 
                if comment_example != "":
                    error_msg += f"\n // the usage of {func_name} is as follows: \n" + comment_example

        return self.fuzz_fix_prompt.format(harness_code=add_lineno_to_code(harness_code, 1), error_msg=error_msg, project_lang=self.project_lang, fuzzer_path=fuzzer_path)
