
from constants import LanguageType, ValResult
import re
from pathlib import Path

#  those errors are only for libfuzzer on c/c++ project
ASANError = [
    "double-free",
    "heap-buffer-overflow",
    "heap-use-after-free",
    "stack-buffer-overflow",
    "stack-buffer-underflow",
    "stack-overflow",
    "global-buffer-overflow",
    "SEGV",
    "dynamic-stack-buffer-overflow",
    "invalid alignment",
    "FPE",
    "requested allocation size",
    "attempting free on address which was not malloc()-ed",
    "memcpy-param-overlap",
    "stack-use-after-scope",
    "negative-size-param",
    "unknown-crash",
    "calloc parameters overflow",
]

LibFuzzerError = [
    "out-of-memory",
    "timeout after",
    "fuzz target exited",
    "overwrites its const input"
]

LeakSanitizerError = [
    "detected memory leaks"
    ]


class CompileErrorExtractor():
    def __init__(self, project_lang: LanguageType):
        self.project_lang = project_lang
        
    def extract_error_message_cpp(self, error_msg: str, range_value: int = 5) -> list[str]:
        error_list = error_msg.split("\n")

        # last error message
        # TODO multiple error messages
      
        index_list:list[int] = []
        for i, line in enumerate(error_list):
            # this pttern
            cpp_pattern =  " error:"
            if cpp_pattern in line.lower():
                index_list.append(i)
        
        # reduce overlap between errors and extract the error message with in the range
        all_error: list[str] = []
        
        for i, line_index in enumerate(index_list):
            

            lower_bound = max(line_index-range_value, 0)
            upper_bound = min(line_index+range_value, len(error_list))
                
    
            # TODO if the line is too long, it may not be the error message, thus can be ignored
            for i, line in enumerate(error_list[lower_bound:upper_bound]):
                # 
                if len(line) > 1000 and "clang" in line:
                    continue
                if line in all_error:
                    continue

                all_error.append(line)

        return all_error


    def extract_error_message(self, error_msg: str) -> list[str]:
        '''Extract the error message from the error message'''
        if self.project_lang in [LanguageType.CPP, LanguageType.C, LanguageType.JAVA]:
            return self.extract_error_message_cpp(error_msg)
        else:
            raise ValueError(f"Unsupported language: {self.project_lang}")
            # return "No error message"


class FuzzLogParser():
    def __init__(self, project_lang: LanguageType):
        self.project_lang = project_lang
        # self.compile_error_extractor = CompileErrorExtractor(project_lang)

    def is_stack_frame(self, index: int, line: str) -> bool:

        if self.project_lang in [LanguageType.C, LanguageType.CPP]:
            pattern = f"#{index} 0x"
            return pattern in line

        if self.project_lang == LanguageType.JAVA:
            pattern = ["Caused by:", "at "]
            return any(p in line for p in pattern)
        
        raise ValueError(f"Unsupported language: {self.project_lang}")
        return False
    
    def parse_log(self, log_file: Path)-> tuple[ValResult, list[str], list[list[str]]]:
        try:
            with open(log_file, "r", encoding="utf-8", errors='ignore') as file:
                log = file.read()
            
            return self.parse_str(log)
        except Exception as e:
            return ValResult.ReadLogError, [str(e)], []
    
    def parse_str(self, log: str) -> tuple[ValResult, list[str], list[list[str]]]:
        """Parse the log file and extract errors."""

        # This only test on libfuzzer for c/c++ project

        # Define the error patterns
        error_patterns = ['ERROR: LeakSanitizer',  'ERROR: libFuzzer:', 'ERROR: AddressSanitizer', "== Java Exception"]
        
        if any(error_pattern in log for error_pattern in error_patterns):
            error_type_line:list[str] = []
            stack_list: list[list[str]] = []
            one_stack:list[str] = []

            stack_index = 0
            # Extract and print the errors
            for line in log.split("\n"):
                if any(error_pattern in line for error_pattern in error_patterns):
                    error_type_line.append(line)

                if self.is_stack_frame(stack_index, line):
                    stack_index += 1
                    one_stack.append(line)
                # if the index break, it means the stack is finished
                else:
                    stack_index = 0
                    if one_stack:
                        stack_list.append(one_stack)
                        one_stack = []

            return ValResult.Crash, error_type_line, stack_list

        # check the code coverage
        
        # Extract the number after `INITED cov:`
        inited_cov = re.search(r"INITED\s+cov:\s+(\d+)", log)
        inited_cov_value = inited_cov.group(1) if inited_cov else None

        # Extract the number after `DONE   cov:`
        done_cov = re.search(r"DONE\s+cov:\s+(\d+)", log)
        done_cov_value = done_cov.group(1) if done_cov else None

        # find the last coverage values as the done_cov_value if done_cov_value is None
        if not done_cov_value:
            done_cov = re.findall(r"cov:\s+(\d+)", log)
            if done_cov:
                done_cov_value = done_cov[-1]


        if not inited_cov_value or not done_cov_value:
            return ValResult.LackCovError, [], []

        if done_cov_value == inited_cov_value:
            return ValResult.ConstantCoverageError, [], []
        
        return ValResult.NoError, [], []


if __name__ == "__main__":
    log_file = "/home/yk/code/LLM-reasoning-agents/outputs_evaluation/gpt5-mini/raw/clamav/cl_scandesc/run1_bnifjesuxjjeqtch/fuzzing0.log"  # Replace with your log file path
    # with open(log_file, "r") as file:
        # log = file.read()
    parser = FuzzLogParser(LanguageType.C)
    res, error_type, stacks = parser.parse_log(Path(log_file))
    print(res)
    print(error_type)
    print(stacks)

    # extract_errors(log_file)

