import time
from agent_tools.code_retriever import CodeRetriever
from bench_cfg import LanguageType
from utils.oss_fuzz_utils import OSSFuzzUtils
from utils.docker_utils import DockerUtils
from constants import CompileResults, LSPFunction
from utils.misc import extract_name, save_code_to_file, remove_color_characters, kill_process
import subprocess as sp
import os
import shutil
from pathlib import Path
from typing import Optional
import random
import re

class Compiler():

    def __init__(self, oss_fuzz_dir: Path, benchmark_dir: Path, project_name: str,
                  new_project_name: str, include_path: Optional[set[str]]=None, 
                  code_retriever: Optional[CodeRetriever]=None, function_signature: str="") -> None:

        self.oss_fuzz_dir = oss_fuzz_dir
        self.project_name = project_name
        self.new_project_name = new_project_name
        self.code_retriever = code_retriever
        self.function_signature = function_signature
        
        self.oss_tool = OSSFuzzUtils(oss_fuzz_dir, benchmark_dir, project_name, new_project_name)
        self.project_lang =  self.oss_tool.get_project_language()
        self.docker_tool = DockerUtils(oss_fuzz_dir, project_name, new_project_name, self.project_lang)

        self.build_harness_cmd = self.oss_tool.get_script_cmd("build_fuzzers")

        # self.build_harness_cmd = ["python", os.path.join(self.oss_fuzz_dir, "infra", "helper.py"),
                            # "build_fuzzers", "--clean", self.new_project_name,  "--", "-fsanitize=fuzzer", "-fsanitize=address", "-fsanitize-coverage=trace-pc-guard"]
        self.build_image_cmd =  self.oss_tool.get_script_cmd("build_image")
        self.include_path: set[str] = include_path if include_path else set()

    def _handle_static_function(self) -> Optional[str]:
       
        if self.code_retriever is None:
            return None
        
        if not self.function_signature:
            return None
            
        func_name = extract_name(self.function_signature, keep_namespace=True, language=self.project_lang)
        
        # get definition
        defs = self.code_retriever.get_symbol_info(func_name, LSPFunction.Definition)
        if not defs:
            defs = self.code_retriever.get_symbol_info(func_name, LSPFunction.Declaration)
        if not defs:
            return None
        
        
        target_def = defs[0]
        file_path = target_def.get("file_path", "")
        print("function information:", defs)
        print("function signature:", self.function_signature)
        print("function name:", func_name)
        print(f"Modifying static function in file: {file_path}")
        # It is static.
        
        # Read the full file content
        full_content = self.code_retriever.docker_tool.exec_in_container(self.code_retriever.container_id, f"cat {file_path}")
        
        if "No such file or directory" in full_content:
             return None

        lines = full_content.splitlines()

        length = len(lines)
        static_flag = False
        
        # Use regex to match function name followed by ( with word boundary
        # properly escape the function name for regex
        func_pattern = re.compile(rf"\b{re.escape(func_name)}\s*\(")

        # Find and modify the line containing the function definition
        for i, line in enumerate(lines[::-1]):
            # search for function name
            match = func_pattern.search(line)
            if match:
                # Check for assignment or other control structures indicating a call
                preceding_text = line[:match.start()]
                
                # If "return", "if", "while", or ending with =, (, [, it's likely a call
                if re.search(r"(\breturn\b|\bif\b|\bwhile\b|\bswitch\b|\bfor\b|[=\(\[])\s*$", preceding_text):
                     static_flag = False
                     continue

                # if static is in the same line, replace it
                if "static" in line:
                    modified_line = line.replace("static", "      ")
                    lines[length-i-1] = modified_line
                    static_flag = False
                elif ";" not in line:
                    # If no static and no semicolon, it might be the definition header (e.g. "void func()")
                    # Enable flag to look for static in previous lines
                    static_flag = True
                else:
                    # It has semicolon but no static. Likely a call "func();" or a non-static declaration.
                    static_flag = False
                
                continue

            # Check if we need to remove static from previous lines (next in iteration)
            if static_flag:
                if "static" in line:
                    should_replace = True
                    if ";" in line:
                        # Ensure static is after the last semicolon
                        if line.rfind("static") < line.rfind(";"):
                            should_replace = False
                            static_flag = False

                    if should_replace:
                        modified_line = line.replace("static", "      ")
                        lines[length-i-1] = modified_line
                        static_flag = False 
                elif ";" in line:
                     # Encountered a semicolon (end of previous statement), stop looking for static for this function
                     static_flag = False

        modified_content = "\n".join(lines)
        
        # Save modified file
        modified_filename = Path(file_path).name
        local_modified_path = self.oss_fuzz_dir / "projects" / self.new_project_name / modified_filename
        save_code_to_file(modified_content, local_modified_path)
        
        return f"COPY {modified_filename} {file_path}"

    def write_dockerfile(self, harness_code: str, harness_path: Path, cmd: Optional[str]=None) -> None:
        '''Copy the harness file to overwrite all existing harness files'''
        # write copy in dockerfile and re-build the image

        docker_file_path = self.oss_fuzz_dir / "projects" / self.new_project_name / 'Dockerfile'
        docker_file_bak = docker_file_path.with_suffix('.bak')

        
        # resotre the old dockerfile
        if docker_file_bak.exists():
            shutil.copy(docker_file_bak, docker_file_path)
        else:
            shutil.copy(docker_file_path, docker_file_bak)

        
        # save the target to the project, generate a random name to prevent caching
        random_str = ''.join(random.choices("abcdefghijklmnopqrstuvwxyz", k=16))
        new_local_name = f"{harness_path.stem}_{random_str}{harness_path.suffix}"
        local_harness_path = self.oss_fuzz_dir / "projects" / self.new_project_name / new_local_name
        save_code_to_file(harness_code, local_harness_path)
        
        # for other files in the project that may include the harness file, we also need to overwrite them
        original_harness_path = self.oss_fuzz_dir / "projects" / self.new_project_name / harness_path.name
        save_code_to_file(harness_code, original_harness_path)

        with open(docker_file_path, 'a') as f:
            # Add additional statement in dockerfile to overwrite with generated fuzzer
            f.write(f'\nCOPY {new_local_name} {harness_path}\n')

            if cmd:
                f.write(f'{cmd}\n')

    def write_build_script(self) -> None:
        '''Copy the harness file to overwrite all existing harness files'''
        # write copy in build.sh and re-build the image
        if not self.include_path:
            return
    
        build_script_path = self.oss_fuzz_dir / "projects" / self.new_project_name / 'build.sh'
        build_script_bak = build_script_path.with_suffix('.bak')

        # resotre the old build script
        if build_script_bak.exists():
            shutil.copy(build_script_bak, build_script_path)
        else:
            shutil.copy(build_script_path, build_script_bak)

        # insert the export CFLAGS after the first line
        all_lines = build_script_path.read_text().splitlines()

        include_flags = ' '.join([f'-I{path}' for path in self.include_path])
        all_lines.insert(1, f'export CFLAGS="$CFLAGS {include_flags}"')
        all_lines.insert(2, f'export CXXFLAGS="$CXXFLAGS {include_flags}"')
        
        with open(build_script_path, 'w') as f:
            f.writelines('\n'.join(all_lines))
        
    def compile_harness(self,  harness_code: str, harness_path: Path, fuzzer_name: str) -> tuple[CompileResults, str]:
        '''Compile the generated harness code'''

        # Run build.sh. There are two possible outcomes:
        # 1. The code compiles successfully. 
        # 3. The code compiles but has a compile error. The code should be fixed.
        cmd: Optional[str] = None
        if self.code_retriever and self.project_lang in [LanguageType.C, LanguageType.CPP]:
            cmd = self._handle_static_function()
        # write the dockerfile
        self.write_dockerfile(harness_code, harness_path, cmd)
        self.write_build_script()

        # build the image
        build_flag = False
        for _ in range(3):  # try multiple times to avoid some issues
            if self.docker_tool.build_image(self.build_image_cmd):
                build_flag = True
                break
            time.sleep(5)
        if not build_flag:
            return CompileResults.ImageError, "Failed to build image"
        
        # recover the dockerfile, so that the harness file is not overwritten

        # run the build command
        process = None
        try:
            # self.docker_tool.run_cmd(["find", "-name", "comp"])
            process = sp.run(self.build_harness_cmd,
                   stdout=sp.PIPE,  # Capture standard output
                    # Important!, build fuzzer error may not appear in stderr, so redirect stderr to stdout
                   stderr=sp.STDOUT,  # Redirect standard error to standard output
                   text=True,  # Get output as text (str) instead of bytes
                   check=True,
                   start_new_session=True
                   ) # Raise exception if build fails

            build_msg = remove_color_characters(process.stdout)
            # succeed to run build command
            fuzzer_name = os.path.join(self.oss_tool.get_path("fuzzer"), fuzzer_name)
            if os.path.exists(fuzzer_name):
                return CompileResults.Success, build_msg
            
            # For net-snmp, compile failed will also not generate the fuzzer but no error is raised
            # TODO: more robust method to check compile error
            else:
                return CompileResults.CodeError, build_msg

        except sp.CalledProcessError as e:
            kill_process(process)
            # remove color characters
            build_msg = remove_color_characters(e.output)
            return CompileResults.CodeError, build_msg

          