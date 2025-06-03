from utils.oss_fuzz_utils import OSSFuzzUtils
from utils.docker_utils import DockerUtils
from constants import CompileResults
from utils.misc import save_code_to_file, remove_color_characters
import subprocess as sp
import os
import shutil
from pathlib import Path

class Compiler():

    def __init__(self, oss_fuzz_dir: Path, project_name: str, new_project_name: str):

        self.oss_fuzz_dir = oss_fuzz_dir
        self.project_name = project_name
        self.new_project_name = new_project_name
       
        self.oss_tool = OSSFuzzUtils(oss_fuzz_dir, project_name, new_project_name)
        self.project_lang =  self.oss_tool.get_project_language()
        self.docker_tool = DockerUtils(oss_fuzz_dir, project_name, new_project_name, self.project_lang)

        self.build_harness_cmd = self.oss_tool.get_script_cmd("build_fuzzers")

        # self.build_harness_cmd = ["python", os.path.join(self.oss_fuzz_dir, "infra", "helper.py"),
                            # "build_fuzzers", "--clean", self.new_project_name,  "--", "-fsanitize=fuzzer", "-fsanitize=address", "-fsanitize-coverage=trace-pc-guard"]
        self.build_image_cmd =  self.oss_tool.get_script_cmd("build_image")

    def write_dockerfile(self, harness_path: Path) -> None:
        '''Copy the harness file to overwrite all existing harness files'''
        # write copy in dockerfile and re-build the image

        docker_file_path = self.oss_fuzz_dir / "projects" / self.new_project_name / 'Dockerfile'
        docker_file_bak = docker_file_path.with_suffix('.bak')

        
        # resotre the old dockerfile
        if docker_file_bak.exists():
            shutil.copy(docker_file_bak, docker_file_path)
        else:
            shutil.copy(docker_file_path, docker_file_bak)

        with open(docker_file_path, 'a') as f:
            # Add additional statement in dockerfile to overwrite with generated fuzzer
            f.write(f'\nCOPY {os.path.basename(harness_path)} {harness_path}\n')


    def compile(self,  harness_code: str, harness_path: Path, fuzzer_name: str) -> tuple[CompileResults, str]:
        '''Compile the generated harness code'''

        # Run build.sh. There are two possible outcomes:
        # 1. The code compiles successfully. 
        # 3. The code compiles but has a compile error. The code should be fixed.

        # write the dockerfile
        self.write_dockerfile(harness_path)
        
        # save the target to the project
        local_harness_path = self.oss_fuzz_dir / "projects" / self.new_project_name / harness_path.name 
        save_code_to_file(harness_code, local_harness_path)


        # build the image
        if not self.docker_tool.build_image(self.build_image_cmd):
            return CompileResults.ImageError, "Failed to build image"
        
        # recover the dockerfile, so that the harness file is not overwritten

        # run the build command
        try:
            sp_result = sp.run(self.build_harness_cmd,
                   stdout=sp.PIPE,  # Capture standard output
                    # Important!, build fuzzer error may not appear in stderr, so redirect stderr to stdout
                   stderr=sp.STDOUT,  # Redirect standard error to standard output
                   text=True,  # Get output as text (str) instead of bytes
                   check=True) # Raise exception if build fails

            build_msg = remove_color_characters(sp_result.stdout)
            # succeed to run build command
            fuzzer_name = os.path.join(self.oss_tool.get_path("fuzzer"), fuzzer_name)
            if os.path.exists(fuzzer_name):
                return CompileResults.Success, build_msg
            else:
                return CompileResults.FuzzerError, build_msg

        except sp.CalledProcessError as e:
        
            # remove color characters
            build_msg = remove_color_characters(e.output)
            return CompileResults.CodeError, build_msg

          