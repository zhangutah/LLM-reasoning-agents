from constants import LanguageType
from agent_tools.code_retriever import CodeRetriever
import logging
from typing import Optional
from bench_cfg import BenchConfig
from ossfuzz_gen import benchmark as benchmarklib
from utils.misc import add_lineno_to_code
from harness_agent.fixing.raw import FixerPromptBuilder
from ossfuzz_gen.build_runner import FuzzingLogParser
from ossfuzz_gen import code_fixer as oss_fuzz_code_fixer

class OSSFUZZFixerPromptBuilder(FixerPromptBuilder):
    # (self.benchcfg, self.project_name, self.new_project_name, self.code_retriever, self.logger,
                                        # local_compile_fix_prompt, local_fuzz_fix_prompt, self.project_lang)
    def __init__(self, benchcfg: BenchConfig, oss_fuzz_benchmark: Optional[benchmarklib.Benchmark], project_name: str, new_project_name: str, code_retriever: CodeRetriever,
                 logger: logging.Logger, compile_fix_prompt: str, fuzz_fix_prompt: str,  project_lang: LanguageType):
        super().__init__(benchcfg, oss_fuzz_benchmark, project_name, new_project_name, code_retriever, logger, compile_fix_prompt, fuzz_fix_prompt, project_lang)

    def build_compile_prompt(self, harness_code: str, error_msg: str, fuzzer_path: str)-> str:
        '''
        Build the prompt for the code fixer. If you need to customize the prompt, you can override this function.
        '''
        error_msg = self.reduce_msg(error_msg)
        try:
            context = oss_fuzz_code_fixer._collect_context(self.oss_fuzz_benchmark, error_msg.splitlines()) # type: ignore
            instruction = oss_fuzz_code_fixer._collect_instructions(self.oss_fuzz_benchmark, error_msg.splitlines(), harness_code) # type: ignore
            error_msg += f"\n\nThe context of the error is: {context}\n"
            error_msg += f"\n\nThe instructions for the error is: {instruction}\n"
        except Exception as e:
            self.logger.error(f"Error occurred while collecting context and instructions for compiling error: {e}")

        return self.compile_fix_prompt.format(harness_code=add_lineno_to_code(harness_code, 1), error_msg=error_msg, project_lang=self.project_lang, fuzzer_path=fuzzer_path)

    def build_fuzz_prompt(self, harness_code: str, error_msg: str, fuzzer_path: str)-> str:
        '''
        Build the prompt for the code fixer. If you need to customize the prompt, you can override this function.
        '''
        # extract 
        #1 0x5639df576b54 in ixmlDocument_createAttributeEx /src/pupnp/ixml/src/document.c:269:26

        try:
            parser = FuzzingLogParser(str(self.benchcfg.oss_fuzz_dir), self.project_name)
    
            # Assuming you have a log file to parse
            parse_result = parser.parse_libfuzzer_logs(error_msg.splitlines(), self.project_name)
            instruction = parse_result[-1]._get_error_desc()  # type: ignore
            error_msg += f"\n\nThe instructions for the error is: {instruction}\n"
        except Exception as e:
            self.logger.error(f"Failed to parse libFuzzer logs: {e}")

        return self.fuzz_fix_prompt.format(harness_code=add_lineno_to_code(harness_code, 1), error_msg=error_msg, project_lang=self.project_lang, fuzzer_path=fuzzer_path)
    