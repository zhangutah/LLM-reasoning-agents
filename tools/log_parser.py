
from constants import LanguageType, FuzzResult
import re
import math

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
    def __init__(self, project_lang):
        self.project_lang = project_lang
        
    def extract_error_message_cpp(self, error_msg: str, range_value: int = 5):
        error_list = error_msg.split("\n")

        # last error message
        # TODO multiple error messages
      
        index_list = []
        for i, line in enumerate(error_list):
            # this pttern
            cpp_pattern =  " error:"
            if cpp_pattern in line.lower():
                index_list.append(i)
        
        # reduce overlap between errors and extract the error message with in the range
        all_error = []
        
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


        


    def extract_error_message(self, error_msg: str) -> list[list[str]]:
        '''Extract the error message from the error message'''
        if self.project_lang in [LanguageType.CPP, LanguageType.C]:
            return self.extract_error_message_cpp(error_msg)
        else:
            raise ValueError(f"Unsupported language: {self.project_lang}")
            # return "No error message"


class FuzzLogParser():
    def __init__(self, project_lang):
        self.project_lang = project_lang
        # self.compile_error_extractor = CompileErrorExtractor(project_lang)

    def parse_log(self, log_file: str):
        try:
            with open(log_file, "r") as file:
                log = file.read()
            
            return self.parse_str(log)
        except Exception as e:
            return FuzzResult.ReadLogError, e, None
    
    def parse_str(self, log: str):

        assert isinstance(log, str), "log must be a string"
        # This only test on libfuzzer for c/c++ project

        # Define the error patterns
        error_patterns = ['ERROR: LeakSanitizer',  'ERROR: libFuzzer:', 'ERROR: AddressSanitizer']
        crash_patterns = "Test unit written to"

        if crash_patterns not in log:
            # check the code coverage
            
            # Extract the number after `INITED cov:`
            inited_cov = re.search(r"INITED cov: (\d+)", log)
            inited_cov_value = inited_cov.group(1) if inited_cov else None

             # Extract the number after `DONE   cov:`
            done_cov = re.search(r"DONE\s+cov:\s+(\d+)", log)
            done_cov_value = done_cov.group(1) if done_cov else None

            assert inited_cov_value, "INITED cov: not found"
            assert done_cov_value, "DONE   cov: not found"

            if done_cov_value == inited_cov_value:
                return FuzzResult.ConstantCoverageError, None, None
            
            return FuzzResult.NoError, None, None


        error_type_line = []
        stack_list = []
        one_stack = []

        stack_index = 0
        # Extract and print the errors
        for line in log.split("\n"):
            if any(error_pattern in line for error_pattern in error_patterns):
                error_type_line.append(line)

            if f"#{stack_index} 0x" in line:
                stack_index += 1
                one_stack.append(line)
            # if the index break, it means the stack is finished
            else:
                stack_index = 0
                if one_stack:
                    stack_list.append(one_stack)
                    one_stack = []

        return FuzzResult.Crash, error_type_line, stack_list

if __name__ == "__main__":
    log_file = "/home/yk/code/fuzz-introspector/scripts/oss-fuzz-gen-e2e/workdir/oss-fuzz-gen/bench_results/output-libpcap-pcap_findalldevs/logs/run/01.c-F3.log"  # Replace with your log file path
    with open(log_file, "r") as file:
        log = file.read()
    parser = FuzzLogParser(LanguageType.CPP)
    res, error_type, stacks = parser.parse(log)
    print(error_type)
    print(stacks)

    # extract_errors(log_file)

