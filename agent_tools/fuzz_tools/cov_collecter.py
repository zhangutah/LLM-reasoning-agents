import os
from agent_tools.fuzz_tools.compiler import Compiler
from utils.docker_utils import DockerUtils
from constants import PROJECT_PATH, CompileResults, COV_WRAP_FILE_NAME, LanguageType, FuzzEntryFunctionMapping
from agent_tools.code_tools.parsers.cpp_parser import CPPParser
from agent_tools.code_tools.parsers.c_parser import CParser
from agent_tools.code_tools.parsers.java_parser import JavaParser
from pathlib import Path
import json
import shutil
import logging
from typing import Optional
from utils.misc import logger_wrapper

class CovCollector():

    def __init__(self, oss_fuzz_dir: Path, benchmark_dir: Path, project_name: str, new_project_name: str,
                  project_lang: LanguageType, logger:Optional[logging.Logger]) -> None:
        
        self.logger = logger
        
        self.oss_fuzz_dir = oss_fuzz_dir
        self.benchmark_dir = benchmark_dir
        self.project_name = project_name
        self.new_project_name = new_project_name      
        self.project_lang = project_lang
        self.docker_utils = DockerUtils(oss_fuzz_dir, project_name, new_project_name, project_lang)
        self.parser = self.get_language_parser()

    def get_language_parser(self):
        if self.project_lang == LanguageType.CPP:
            return CPPParser
        elif self.project_lang == LanguageType.C:
            return CParser
        elif self.project_lang == LanguageType.JAVA:
            return JavaParser
        else:
            raise Exception(f"Language {self.project_lang} not supported.")
        
    def gen_wrapped_code(self, harness_code: str, function_name: str) -> str:
        # add the wrapper code to the harness code
        wrap_file = Path(f"{PROJECT_PATH}/agent_tools/fuzz_tools/{COV_WRAP_FILE_NAME}_{self.project_lang.value.lower()}.txt")
        if not wrap_file.exists():
            logger_wrapper(self.logger, f"Wrapper file {wrap_file} does not exist", level="error")
            return harness_code
        
        wrap_code = wrap_file.read_text()
        
        # find the fuzz entry
        parser = self.parser(None, harness_code, self.project_lang)
        fuzz_node = parser.get_fuzz_function_node(function_name)
        if fuzz_node:
            fuzz_start_row, fuzz_start_col, fuzz_end_row = fuzz_node.start_point.row, fuzz_node.start_point.column, fuzz_node.end_point.row
        else:
            logger_wrapper(self.logger, f"Fuzz function {function_name} not found", level="error")
            raise Exception(f"Fuzz function {function_name} not found")
        
        # add reset_sancov_counters before fuzz function
        lines = harness_code.splitlines()
        
        # TODO: fix indent for python
        indent = " " * fuzz_start_col

        # add save_sancov_counters after fuzz function
        lines.insert(fuzz_end_row + 1, f"{indent}save_sancov_counters();")
        lines.insert(fuzz_start_row, f"{indent}reset_sancov_counters();")

        # insert the wrapper code before the fuzz entry
        entry_function = FuzzEntryFunctionMapping[self.project_lang]
        entry_node = parser.get_definition_node(entry_function)
        if not entry_node:
            raise Exception(f"Entry function {entry_function} not found")
        
        lines.insert(entry_node.start_point.row, wrap_code)
        harness_code =  "\n".join(lines)

        return harness_code


    def recompile(self, harness_code: str,  harness_path: Path, fuzzer_name: str, function_name: str) -> bool:
        if self.project_lang in [LanguageType.C, LanguageType.CPP]:
            wrapped_code = self.gen_wrapped_code(harness_code, function_name)
        else:
            logger_wrapper(self.logger, f"Language {self.project_lang} not supported for now", level="error")
            raise Exception(f"Language {self.project_lang} not supported for now")

        # init the compiler
        compiler = Compiler(self.oss_fuzz_dir, self.benchmark_dir,self.project_name, self.new_project_name)
        # compile the code
        compile_res, build_msg = compiler.compile(wrapped_code, harness_path, fuzzer_name)
        if compile_res != CompileResults.Success:
            logger_wrapper(self.logger, f"Compile error: {build_msg}", level="error")
            return False
    
        # run fuzzer driver with testcase
        return True
    
    def clean_workspace(self):
        '''Clean the workspace'''
        try:        
            # first remove the out directory
            self.docker_utils.clean_build_dir()
            # remove the docker image here
            self.docker_utils.remove_image()
            # remove the project directory
            shutil.rmtree(os.path.join(self.oss_fuzz_dir, "projects", self.new_project_name))
            # clean the build directory
            shutil.rmtree(os.path.join(self.oss_fuzz_dir, "build", "out", self.new_project_name))

        except:
            pass

    # ./inchi_input_fuzzer -print_coverage=1 -runs=1  -timeout=100  ./corpora/ 2>&1 | grep inchi_dll.c | grep -w COVERED_FUNC | grep {}
    # ls -ltr
    def collect_coverage(self, harness_code: str, harness_path: Path, fuzzer_name: str,
                          function_name: str, corpora_dir: Path) -> tuple[int, int, bool]:

        flag = self.recompile(harness_code, harness_path, fuzzer_name, function_name)
        if not flag:
            logger_wrapper(self.logger, f"Recompile error: {flag}", level="error")
            return 0, 0, False
        # run the call back
        cmd = ["python", "cov_c.py", "--fuzzer-name", fuzzer_name, 
                "--corpus-dir", "./corpora/"]
        local_out =  Path(self.oss_fuzz_dir) / "build" / "out" / self.new_project_name

        # copy the cov_c.py to the out directory
        shutil.copy(Path(PROJECT_PATH) / "agent_tools" / "fuzz_tools" / "cov_c.py", local_out / "cov_c.py")
        shutil.copy(Path(PROJECT_PATH) / "agent_tools" / "fuzz_tools" / "cov_wrap_code_c.txt", local_out / "cov_wrap_code_c.txt")
        volumes = {local_out: {"bind": "/out", "mode": "rw"},
                   corpora_dir: {"bind": "/out/corpora", "mode": "rw"}}
        
        self.docker_utils.run_cmd(cmd, volumes=volumes, working_dir="/out")

        cov_path = local_out / "cov.json"
        if not cov_path.exists():
            logger_wrapper(self.logger, f"Coverage file {cov_path} does not exist", level="error")
            return 0, 0, False
        
        with open(cov_path, "r") as f:
            cov = json.load(f)

            msg = cov.get("msg", "")
            if msg != "Success":
                logger_wrapper(self.logger, f"Error running the coverage file: {msg}", level="error")
                return 0, 0, False
            
            init_cov, final_cov = cov.get("init_cov", 0), cov.get("final_cov", 0)
            if init_cov != 0 and final_cov > init_cov:
                return init_cov, final_cov, True
            else:
                return init_cov, final_cov, False