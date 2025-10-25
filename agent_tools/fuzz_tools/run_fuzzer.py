import os
import subprocess as sp
from agent_tools.fuzz_tools.log_parser import FuzzLogParser
from constants import ValResult, LanguageType
import time
from pathlib import Path

class FuzzerRunner():

    def __init__(self, oss_fuzz_dir: Path, new_project_name: str,
                 project_lang: LanguageType, run_timeout: int , save_dir: Path):
        
        self.oss_fuzz_dir = oss_fuzz_dir
        self.new_project_name = new_project_name
        self.run_timeout = run_timeout*60  # convert to seconds
        self.save_dir = save_dir
        self.project_lang = project_lang

        
    def run_fuzzing(self, counter: int, fuzzer_name: str) -> tuple[ValResult, list[str], list[list[str]]]:
        # run the fuzzing
        print(f"Running fuzzer {fuzzer_name} for {self.new_project_name}...")
        # create the corpus directory
        corpus_dir = os.path.join(self.save_dir, "corpora")
        if not os.path.exists(corpus_dir):
            os.makedirs(corpus_dir)

        # this is copied from the oss-fuzz-gen, thanks to the author
        command = [ 'python3',  os.path.join(self.oss_fuzz_dir, "infra", "helper.py"), 'run_fuzzer',
                    '--corpus-dir', corpus_dir,
                      self.new_project_name, fuzzer_name, '--',
                    '-print_final_stats=1',
                    f'-max_total_time={self.run_timeout}',
                        # Without this flag, libFuzzer only consider short inputs in short
                        # experiments, which lowers the coverage for quick performance tests.
                    '-len_control=0',
                        # Timeout per testcase.
                    '-timeout=30',
                    '-detect_leaks=0',
                    '-seed=1234'
            ]
        log_file_path = self.save_dir / f"fuzzing{counter}.log"
      # Define the error patterns
        error_patterns = ['ERROR: LeakSanitizer',  'ERROR: libFuzzer:', 'ERROR: AddressSanitizer']
        

        try:
            # Run process and filter output
            process = sp.Popen(command,
                stdout=sp.PIPE,
                stderr=sp.STDOUT,
                bufsize=0  # Unbuffered for real-time output
            )
            
            with open(log_file_path, "w", encoding='utf-8', errors='ignore') as log_file:
            
                inited_found = False
                done_found = False
                crash_found = False
                # Read line by line from binary output
                for line_bytes in iter(process.stdout.readline, b''):
                    # Decode with error handling - replace invalid chars
                    line = line_bytes.decode('utf-8', errors='ignore')
                    # log_file.write(line)
                    # log_file.flush()
                    if "INITED" in line:
                        inited_found = True
                    # Check for DONE marker  
                    elif "DONE" in line:
                        done_found = True
                    elif any(error_pattern in line for error_pattern in error_patterns):
                        crash_found = True
                    # 
                    if not inited_found or done_found or crash_found:
                        log_file.write(line)
                        log_file.flush()
                    # Between INITED and DONE or Between INITED and CRASH, only keep lines with "#"
                    else:
                        if "#" in line and "cov" in line:
                            log_file.write(line)
                            log_file.flush()
                            
            process.wait(timeout=self.run_timeout + 5)

            return FuzzLogParser(self.project_lang).parse_log(log_file_path)
            
        except sp.TimeoutExpired:
            # sleep some time to make sure the log file is written, otherwise, some part of the log file may be missing
            time.sleep(1)
            return FuzzLogParser(self.project_lang).parse_log(log_file_path)
        except Exception:
            return FuzzLogParser(self.project_lang).parse_log(log_file_path)