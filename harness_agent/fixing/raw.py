from constants import LanguageType, CompileResults
from agent_tools.code_retriever import CodeRetriever
import logging
import tiktoken
from typing import Any, Optional
from bench_cfg import BenchConfig
from ossfuzz_gen import benchmark as benchmarklib
from utils.misc import add_lineno_to_code

class FixerPromptBuilder:
    # (self.benchcfg, self.project_name, self.new_project_name, self.code_retriever, self.logger,
                                        # local_compile_fix_prompt, local_fuzz_fix_prompt, self.project_lang)
    def __init__(self, benchcfg: BenchConfig, oss_fuzz_benchmark: Optional[benchmarklib.Benchmark], project_name: str, new_project_name: str, code_retriever: CodeRetriever,
                 logger: logging.Logger, compile_fix_prompt: str, fuzz_fix_prompt: str,  project_lang: LanguageType):

        self.benchcfg = benchcfg
        self.oss_fuzz_benchmark = oss_fuzz_benchmark
        self.new_project_name = new_project_name
        self.project_name = project_name
        self.code_retriever = code_retriever
        self.logger = logger
        self.compile_fix_prompt = compile_fix_prompt
        self.fuzz_fix_prompt = fuzz_fix_prompt
        assert self.benchcfg.fixing_mode in ["raw", "oss_fuzz", "issta", "agent"], "fixing mode must be one of ['raw', 'oss_fuzz', 'issta', 'agent']"
        assert self.benchcfg.header_mode in ["static", "all", "agent", "oss_fuzz", "no"], "header mode must be one of ['static', 'all', 'agent', 'oss_fuzz', 'no']"

        self.project_lang = project_lang

    def reduce_msg(self, error_msg: str) -> str:
        enc = tiktoken.encoding_for_model("gpt-4o")
        while len(enc.encode(error_msg)) > self.benchcfg.model_token_limit // 2:
            # remove the first line until the error message is short enough
            error_msg = error_msg.split("\n", 1)[1]
        return error_msg

    def build_compile_prompt(self, harness_code: str, error_msg: str, fuzzer_path: str)-> str:
        '''
        Build the prompt for the code fixer. If you need to customize the prompt, you can override this function.
        '''
        error_msg = self.reduce_msg(error_msg)
        return self.compile_fix_prompt.format(harness_code=add_lineno_to_code(harness_code, 1), error_msg=error_msg, project_lang=self.project_lang, fuzzer_path=fuzzer_path)

    def build_fuzz_prompt(self, harness_code: str, error_msg: str, fuzzer_path: str)-> str:
        '''
        Build the prompt for the code fixer. If you need to customize the prompt, you can override this function.
        '''
        return self.fuzz_fix_prompt.format(harness_code=add_lineno_to_code(harness_code, 1), error_msg=error_msg, project_lang=self.project_lang, fuzzer_path=fuzzer_path)

    def respond(self, state: dict[str, Any]) -> dict[str, Any]:
        fix_counter = state.get("fix_counter", 0)
        last_message = state["messages"][-1].content
        if fix_counter == 0 or self.benchcfg.clear_msg_flag:
            # clear previous messages, need to build the fix prompt based on the provided template 
            state["messages"].clear()
            if last_message.startswith(CompileResults.CodeError.value):
                fix_prompt = self.build_compile_prompt(state["harness_code"], state["build_msg"], state["fuzzer_path"])
            else:
                fix_prompt = self.build_fuzz_prompt(state["harness_code"], state["fuzz_msg"], state["fuzzer_path"])
        else:
            # keep the previous messages, just add the error message
            if last_message.startswith(CompileResults.CodeError.value):
                fix_prompt = "Complie Error Messages:\n" + state["build_msg"]
            else:
                fix_prompt = "Fuzz Error Messages:\n" + state["fuzz_msg"]

        return {"messages": ("user", fix_prompt)}
