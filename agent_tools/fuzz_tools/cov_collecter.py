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
from utils.misc import get_ext_lang
from agent_tools.code_retriever import CodeRetriever

class CovCollector():

    def __init__(self, oss_fuzz_dir: Path, benchmark_dir: Path, project_name: str, new_project_name: str, project_lang: LanguageType, 
                  include_path: Optional[set[str]]=None, code_retriever: Optional[CodeRetriever]=None, function_signature: str="", 
                  logger:Optional[logging.Logger]=None) -> None:
        
        self.logger = logger
        
        self.oss_fuzz_dir = oss_fuzz_dir
        self.benchmark_dir = benchmark_dir
        self.project_name = project_name
        self.new_project_name = new_project_name      
        self.project_lang = project_lang
        self.include_path: set[str] = include_path if include_path else set()
        self.code_retriever = code_retriever
        self.function_signature = function_signature
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

    def gen_wrapped_code(self, harness_code: str, function_name: str, harness_lang: LanguageType) -> str:
        # add the wrapper code to the harness code
        wrap_file = Path(f"{PROJECT_PATH}/agent_tools/fuzz_tools/{COV_WRAP_FILE_NAME}_{harness_lang.value.lower()}.txt")
        if not wrap_file.exists():
            self.logger.error(f"Wrapper file {wrap_file} does not exist") if self.logger else None
            return harness_code
        
        wrap_code = wrap_file.read_text()
        
        # find the fuzz entry
        parser = self.parser(None, harness_code)
        fuzz_node = parser.get_fuzz_function_node(function_name, expression_flag=True)
        if not fuzz_node:
            fuzz_node = parser.get_fuzz_function_node(function_name)

        if fuzz_node:
            fuzz_start_row, fuzz_start_col, fuzz_end_row = fuzz_node.start_point.row, fuzz_node.start_point.column, fuzz_node.end_point.row
        else:
            self.logger.error(f"Fuzz function {function_name} not found") if self.logger else None
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
        
        lines.insert(0, wrap_code)
        harness_code =  "\n".join(lines)
        # add new line at the end
        return harness_code+"\n"


    def gen_wrapped_code_java(self, harness_code: str, function_name: str) -> str:
        """Generate wrapped Java harness code with coverage instrumentation.
        
        Inserts coverage wrapper static fields and methods directly into the fuzzer class.
        No inner class is used to avoid class file issues in OSS-Fuzz.
        """
        wrap_file = Path(f"{PROJECT_PATH}/agent_tools/fuzz_tools/{COV_WRAP_FILE_NAME}_{LanguageType.JAVA.value.lower()}.txt")
        if not wrap_file.exists():
            self.logger.error(f"Wrapper file {wrap_file} does not exist") if self.logger else None
            return harness_code
        
        wrap_code = wrap_file.read_text()
        
        # Find the fuzz entry function and target function call
        parser = self.parser(None, harness_code)
        fuzz_node = parser.get_fuzz_function_node(function_name, expression_flag=True)
        if not fuzz_node:
            fuzz_node = parser.get_fuzz_function_node(function_name)

        if fuzz_node:
            fuzz_start_row, fuzz_start_col, fuzz_end_row = fuzz_node.start_point.row, fuzz_node.start_point.column, fuzz_node.end_point.row
        else:
            self.logger.error(f"Fuzz function {function_name} not found") if self.logger else None
            raise Exception(f"Fuzz function {function_name} not found")
        
        lines = harness_code.splitlines()
        indent = " " * fuzz_start_col

        # Add coverage save after target function, reset before
        # Note: methods are now directly in the class, not in CoverageWrapper
        lines.insert(fuzz_end_row + 1, f"{indent}saveSancovCounters();")
        lines.insert(fuzz_start_row, f"{indent}resetSancovCounters();")

        # Find the fuzzer class and insert coverage wrapper fields/methods directly
        # Look for the class body opening brace
        class_body_start = -1
        for i, line in enumerate(lines):
            stripped = line.strip()
            if ("public class" in stripped or "class " in stripped) and "{" in stripped:
                class_body_start = i
                break
            elif ("public class" in stripped or "class " in stripped):
                # Class declaration without brace on same line, find the brace
                for j in range(i + 1, len(lines)):
                    if "{" in lines[j]:
                        class_body_start = j
                        break
                break
        
        if class_body_start == -1:
            self.logger.error("Could not find fuzzer class body") if self.logger else None
            raise Exception("Could not find fuzzer class body to insert coverage wrapper")
        
        # Indent the wrapper code to be inside the class (add 4 spaces to each line)
        indented_wrap_code = "\n".join("    " + line if line.strip() else line for line in wrap_code.splitlines())
        
        # Insert the wrapper fields/methods right after the class opening brace
        lines.insert(class_body_start + 1, "\n" + indented_wrap_code + "\n")
        
        harness_code = "\n".join(lines)
        return harness_code + "\n"

    def recompile(self, harness_code: str,  harness_path: Path, fuzzer_name: str, function_name: str) -> bool:
        
        harness_lang = get_ext_lang(harness_path)

        if harness_lang in [LanguageType.C, LanguageType.CPP]:
            wrapped_code = self.gen_wrapped_code(harness_code, function_name, harness_lang)
        elif harness_lang == LanguageType.JAVA:
            wrapped_code = self.gen_wrapped_code_java(harness_code, function_name)
        else:
            self.logger.error(f"Language {harness_lang} not supported for now") if self.logger else None
            raise Exception(f"Language {harness_lang} not supported for now")

        # init the compiler
        compiler = Compiler(self.oss_fuzz_dir, self.benchmark_dir,self.project_name, self.new_project_name, include_path=self.include_path,
                             code_retriever=self.code_retriever, function_signature=self.function_signature)
        # compile the code
        compile_res, build_msg = compiler.compile_harness(wrapped_code, harness_path, fuzzer_name)
        if compile_res != CompileResults.Success:
            self.logger.error(f"Compile error: msg is {build_msg}") if self.logger else None
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
            self.logger.error(f"Recompile error: {flag}") if self.logger else None
            return 0, 0, False
        # run the call back
        if self.project_lang in [LanguageType.C, LanguageType.CPP]:
            cov_file = "cov_c.py"
        elif self.project_lang == LanguageType.JAVA:
            cov_file = "cov_jvm.py"
        else:
            self.logger.error(f"Language {self.project_lang} not supported for coverage collection") if self.logger else None
            return 0, 0, False

        cmd = ["python", cov_file, "--fuzzer-name", fuzzer_name, "--corpus-dir", "./corpora/"]
        local_out =  Path(self.oss_fuzz_dir) / "build" / "out" / self.new_project_name

        # copy the cov_c.py to the out directory
        shutil.copy(Path(PROJECT_PATH) / "agent_tools" / "fuzz_tools" / cov_file, local_out / cov_file)
        
        # shutil.copy(Path(PROJECT_PATH) / "agent_tools" / "fuzz_tools" / "cov_wrap_code_c.txt", local_out / "cov_wrap_code_c.txt")
        volumes = {local_out: {"bind": "/out", "mode": "rw"},
                   corpora_dir: {"bind": "/out/corpora", "mode": "rw"}}
        # we should not set the timeout too small, otherwise, the fuzzer may not finish
        msg = self.docker_utils.run_cmd(cmd, volumes=volumes, working_dir="/out", timeout=600)
        if "docker error" in msg.lower():
            self.logger.error(f"Docker Error running the coverage collection: {msg}") if self.logger else None
            return 0, 0, False
        
        # sleep sev
        cov_path = local_out / "cov.json"
        if not cov_path.exists():
            self.logger.error(f"Coverage file {cov_path} does not exist") if self.logger else None
            return 0, 0, False
        
        with open(cov_path, "r") as f:
            cov = json.load(f)

            msg = cov.get("msg", "")
            if msg != "Success":
                self.logger.error(f"Error running the coverage file: {msg}") if self.logger else None
                return 0, 0, False
            
            init_cov, final_cov = cov.get("init_cov", 0), cov.get("final_cov", 0)
            if init_cov != 0 and final_cov > init_cov:
                return init_cov, final_cov, True
            else:
                return init_cov, final_cov, False
            
if __name__ == "__main__":

    # test the cov collector
    oss_fuzz_dir = Path("/home/yk/code/oss-fuzz/")
    benchmark_dir = Path("/home/yk/code/LLM-reasoning-agents/benchmark-sets/function_0/")    
    save_dir = Path("/home/yk/code/LLM-reasoning-agents/outputs_evaluation/gpt5-mini/raw")
    project_name = "icu"
    cov = CovCollector(
        oss_fuzz_dir=oss_fuzz_dir,
        benchmark_dir=benchmark_dir,
        project_name=project_name,
        new_project_name="",
        project_lang=LanguageType.CPP,
        logger=None
    )

    # harness_file = Path("/home/yk/code/LLM-reasoning-agents/outputs_wild/gpt5-mini/agent/double-conversion/double_conversion_stringtodoubleconverter_stringtodouble/run1_tcbknjjcvifgvpiu/harness.txt")
    # harness_file = Path("/home/yk/code/LLM-reasoning-agents/outputs_wild/gpt5-mini/agent/dng_sdk/safeuint32mult/run1_xmnvadoqhamzuobb/harness.txt")
    harness_file = Path("/home/yk/code/LLM-reasoning-agents/outputs_wild/gpt5-mini/raw/icu/icu_76_message2_standardfunctions_datetime_format/run3_gqhquqnejeigukff/harness.txt")
    
    cov.gen_wrapped_code(harness_file.read_text(), "icu_76::message2::StandardFunctions::DateTime::format", LanguageType.CPP)