import logging
from typing import Any
from pathlib import Path
from langgraph.graph import END # type: ignore
from agent_tools.code_tools.parsers.java_parser import JavaParser
from constants import LanguageType
from typing import Any
from agent_tools.fuzz_tools.run_fuzzer import FuzzerRunner
from constants import ValResult
from agent_tools.code_tools.parsers.cpp_parser import CPPParser
from agent_tools.code_tools.parsers.c_parser import CParser
from utils.misc import extract_name

FUZZMSG = {
    ValResult.ConstantCoverageError: "The above code can be built successfully but its fuzzing seems not effective since the coverage never change. Please make sure the fuzz data is used.",
    ValResult.ReadLogError: "The above code can be built successfully but it generates a extreme large log which indicates the fuzz driver may include some bugs. Please do not print any information. ",
    ValResult.LackCovError: "The above code can be built successfully but its fuzzing seems not effective since it lack the initial or final code coverage info. Please make sure the fuzz data is used.",
    ValResult.NoCall: "The above code can be built successfully but the fuzz function is not called in the harness. Please make sure to call the fuzz function.",
    ValResult.Fake: "The above code can be built successfully but you use fake definition. Please make sure to use the target fuzz function from project."
}
class Validation(FuzzerRunner):
    def __init__(self, oss_fuzz_dir: Path, new_project_name: str,
                 project_lang: LanguageType, run_timeout: int , 
                 save_dir: Path, logger: logging.Logger):

        super().__init__(oss_fuzz_dir, new_project_name, project_lang, run_timeout, save_dir)
        self.logger = logger
        self.parser = self.get_language_parser()
            
        
    def get_language_parser(self) -> Any:
        if self.project_lang in [LanguageType.C]:
            return CParser
        elif self.project_lang in [LanguageType.CPP]:
            return CPPParser
        elif self.project_lang == LanguageType.JAVA:
            return JavaParser
        else:
            raise Exception(f"Language {self.project_lang} not supported.")

        
    def run_fuzzing(self, state: dict[str, Any]) -> dict[str, Any]: # type: ignore
        fix_counter = state.get("fix_counter", 0)
        fuzzer_name = state.get("fuzzer_name", "")

        # do static validation  first, if it fails, return directly

        parser = self.parser(file_path=None, source_code=state.get("harness_code", ""))

        function_name = extract_name(state.get("function_signature", ""))
        if parser.exist_function_definition(function_name):
            self.logger.info(f"The function {function_name} is defined in the harness code.")
            return {"messages": ("user", ValResult.Fake), "fuzz_msg": FUZZMSG.get(ValResult.Fake, "")}
        
        if not parser.is_fuzz_function_called(function_name):
            self.logger.info(f"The function {function_name} is not called in the harness code.")
            return {"messages": ("user", ValResult.NoCall), "fuzz_msg": FUZZMSG.get(ValResult.NoCall, "")}

        self.logger.info(f"Run {fix_counter}th Fuzzer for {self.new_project_name}:{fuzzer_name}")
        fuzz_res, error_type_line, stack_list = super().run_fuzzing(fix_counter, fuzzer_name)
        self.logger.info(f"Fuzz res:{fuzz_res.value}, {error_type_line} for {self.new_project_name}:{fuzzer_name}")

        # unable to fix the code
        if fuzz_res == ValResult.RunError:
            return {"messages": ("user", END + "Run Error")}
        elif fuzz_res in [ValResult.ConstantCoverageError, ValResult.LackCovError,  ValResult.ReadLogError]:
            return {"messages": ("user", fuzz_res), "fuzz_msg": FUZZMSG.get(fuzz_res, "")}
        elif fuzz_res == ValResult.Crash:
            # extract the first error message
            error_type = error_type_line[0] if len(error_type_line) > 0 else "Unknown Crash, Unable to extract the error message"
            first_stack = stack_list[0] if len(stack_list) > 0 else ["Unknown Crash, Unable to extract the stack trace"]
            fuzz_error_msg = error_type + "\n" + "\n".join(first_stack)
            return {"messages": ("user", fuzz_res), "fuzz_msg": fuzz_error_msg}
        else:
            return {"messages": ("user", fuzz_res)}


