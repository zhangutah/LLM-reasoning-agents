import os
import subprocess as sp
import re
from tools.log_parser import FuzzLogParser
from utils.misc import remove_color_characters
from constants import FuzzResult

class FuzzerRunner():

    def __init__(self, oss_fuzz_dir: str, new_project_name: str,
                 fuzzer_name: str, project_lang: str, run_timeout: int , save_dir: str ):
        
        self.oss_fuzz_dir = oss_fuzz_dir
        self.new_project_name = new_project_name
        # this name must include file extension
        self.fuzzer_name  = fuzzer_name
        self.run_timeout = run_timeout*60  # convert to seconds
        self.save_dir = save_dir
        self.counter = 0
        self.project_lang = project_lang

        
    def run_fuzzing(self) -> FuzzResult:
        # run the fuzzing

        # create the corpus directory
        corpus_dir = os.path.join(self.save_dir, "corpora")
        if not os.path.exists(corpus_dir):
            os.makedirs(corpus_dir)

        # this is copied from the oss-fuzz-gen, thanks to the author
        command = [ 'python3', f'{self.oss_fuzz_dir}/infra/helper.py', 'run_fuzzer',
                    '--corpus-dir', corpus_dir,
                      self.new_project_name, self.fuzzer_name, '--',
                    '-print_final_stats=1',
                    f'-max_total_time={self.run_timeout}',
                        # Without this flag, libFuzzer only consider short inputs in short
                        # experiments, which lowers the coverage for quick performance tests.
                    '-len_control=0',
                        # Timeout per testcase.
                    '-timeout=30',
            ]

        try:
            # save the fuzz log to a file
            self.counter += 1
            log_file_path = os.path.join(self.save_dir, f"fuzzing{self.counter}.log")
            with open(log_file_path, "w") as log_file:
                sp.run(command,
                    stdout=log_file,  # Capture standard output
                    # Important!, build fuzzer error may not appear in stderr, so redirect stderr to stdout
                    stderr=sp.STDOUT,  # Redirect standard error to standard output
                    text=True,  # Get output as text (str) instead of bytes
                    check=True,
                    timeout=self.run_timeout + 5,  # Set timeout
                    )

            # read the log file
            return FuzzLogParser(self.project_lang).parse_log(log_file_path)
          
        except Exception as e:
            # print(e)
            # timeout error can also capture from the log file
            return FuzzLogParser(self.project_lang).parse_log(log_file_path)