from utils.oss_fuzz_utils import OSSFuzzUtils
from utils.docker_utils import DockerUtils
from constants import CompileResults, LanguageType
from utils.misc import save_code_to_file, remove_color_characters
import subprocess as sp
import os
import shutil

class Compiler():

    def __init__(self, oss_fuzz_dir: str, project_name: str, new_project_name: str):

        self.oss_fuzz_dir = oss_fuzz_dir
        self.project_name = project_name
        self.new_project_name = new_project_name


        self.oss_tool = OSSFuzzUtils(oss_fuzz_dir, project_name, new_project_name)
        self.project_lang =  self.oss_tool.get_project_language()


        self.docker_tool = DockerUtils(oss_fuzz_dir, project_name, new_project_name, self.project_lang)

        self.project_fuzzer_name, self.project_harness_path = self.oss_tool.get_harness_and_fuzzer()

        self.harness_path = os.path.join(self.oss_fuzz_dir, "projects", self.new_project_name, os.path.basename(self.project_harness_path))

        self.build_harness_cmd = self.oss_tool.get_script_cmd("build_fuzzers")
        self.build_image_cmd =  self.oss_tool.get_script_cmd("build_image")

    def write_dockerfile(self):
        '''Copy the harness file to overwrite all existing harness files'''
        # write copy in dockerfile and re-build the image

        docker_file_path = os.path.join(self.oss_fuzz_dir,  "projects", self.new_project_name, 'Dockerfile')
        
        if os.path.exists(docker_file_path + '.bak'):
            # resotre the old dockerfile
            shutil.copy(docker_file_path + '.bak', docker_file_path)
        else:
            # save old dockerfile
            shutil.copy(docker_file_path, docker_file_path + '.bak')

        with open(docker_file_path, 'a') as f:
            # Add additional statement in dockerfile to overwrite with generated fuzzer
            f.write(f'\nCOPY {os.path.basename( self.project_harness_path)} {self.project_harness_path}\n')

    def recover_dockerfile(self):
        '''Recover the dockerfile to the original state'''
        docker_file_path = os.path.join(self.oss_fuzz_dir, "projects", self.new_project_name, 'Dockerfile')
        if os.path.exists(docker_file_path + '.bak'):
            shutil.copy(docker_file_path + '.bak', docker_file_path)

    def compile_harness(self,  harness_code: str):
        '''Compile the generated harness code'''

        # Run build.sh. There are two possible outcomes:
        # 1. The code compiles successfully. 
        # 3. The code compiles but has a compile error. The code should be fixed.

        self.write_dockerfile()
        # save the target to the project
        save_code_to_file(harness_code, self.harness_path)

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
            fuzzer_name = os.path.join(self.oss_tool.get_path("fuzzer"), self.project_fuzzer_name)
            if os.path.exists(fuzzer_name):
                return CompileResults.Success, build_msg
            else:
                return CompileResults.FuzzerError, build_msg

        except sp.CalledProcessError as e:
        
            # remove color characters
            build_msg = remove_color_characters(e.output)
            return CompileResults.CodeError, build_msg

          