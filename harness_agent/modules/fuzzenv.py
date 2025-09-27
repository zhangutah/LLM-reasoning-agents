import os
import random
from constants import PROJECT_PATH
import logging
import shutil
import json
from utils.misc import extract_name
from agent_tools.code_retriever import CodeRetriever
from constants import ALL_FILE_EXTENSION, DockerResults
from utils.oss_fuzz_utils import OSSFuzzUtils
from utils.docker_utils import DockerUtils
from pathlib import Path
from bench_cfg import BenchConfig


class FuzzENV():

    def __init__(self,  benchcfg: BenchConfig, function_signature: str, project_name: str, n_run: int):
        self.benchcfg = benchcfg
        self.eailier_stop_flag = False

        self.project_name = project_name
        self.function_signature = function_signature
        self.n_run = n_run
        # random generate a string for new project name
        random_str = ''.join(random.choices("abcdefghijklmnopqrstuvwxyz", k=16))
        self.new_project_name = f"run{n_run}_{random_str}"
        function_name = extract_name(function_signature, keep_namespace=True)
        function_name = function_name.replace("::", "_")  # replace namespace with underscore
        self.save_dir = self.benchcfg.save_root / project_name.lower() / function_name.lower() / self.new_project_name
        self.logger = self.setup_logging()

        self.oss_tool = OSSFuzzUtils(self.benchcfg.oss_fuzz_dir, self.benchcfg.benchmark_dir, self.project_name, self.new_project_name)
        self.project_lang = self.oss_tool.get_project_language()

        self.docker_tool = DockerUtils(self.benchcfg.oss_fuzz_dir, self.project_name, self.new_project_name, self.project_lang)
        self.init_workspace()
        self.code_retriever = CodeRetriever(self.benchcfg.oss_fuzz_dir, self.project_name, self.new_project_name, self.project_lang, self.benchcfg.usage_token_limit, self.benchcfg.cache_root, self.logger)

        # collect all harness_path, fuzzer pairs
        _fuzzer_name,  _harness_path = self.oss_tool.get_harness_and_fuzzer()
        self.harness_pairs = self.cache_harness_fuzzer_pairs()
        if  _fuzzer_name not in self.harness_pairs:
            self.harness_pairs[_fuzzer_name] = _harness_path
        else:
            # move the harness file to the first
            self.harness_pairs = {_fuzzer_name: _harness_path, **self.harness_pairs}
        self.logger.info(f"Show harness_fuzzer_pairs.json, content:{self.harness_pairs}")

    def setup_logging(self):

        # Create a logger
        logger = logging.getLogger(self.new_project_name)
        logger.setLevel(logging.INFO)

        # create the log file
        if not os.path.exists(self.save_dir):
            os.makedirs(self.save_dir)

        # create the log file
        log_file = self.save_dir / 'agent.log'
        log_file.touch(exist_ok=True)

        # Create a file handler
        file_handler = logging.FileHandler(log_file)
        file_handler.setLevel(logging.INFO)

        # Create a console handler
        console_handler = logging.StreamHandler()
        console_handler.setLevel(logging.INFO)

        # Create a formatter and set it for both handlers
        formatter = logging.Formatter('%(asctime)s - %(levelname)s - %(message)s (%(filename)s:%(lineno)d)')
        file_handler.setFormatter(formatter)
        console_handler.setFormatter(formatter)

        # Add the handlers to the logger
        logger.addHandler(file_handler)
        logger.addHandler(console_handler)

        return logger

    def init_workspace(self):
        '''Initialize the workspace'''

        dst_path = self.benchcfg.oss_fuzz_dir / "projects" / self.new_project_name
        if dst_path.exists():
            # clean the directory
            shutil.rmtree(dst_path)

        # copy a backup of the project
        scr_path = self.benchcfg.oss_fuzz_dir / "projects" / self.project_name
        shutil.copytree(scr_path, dst_path, dirs_exist_ok=True)

        shutil.copy(os.path.join(PROJECT_PATH, "constants.py"), dst_path)

        # save the function name
        with open(self.save_dir / "function.txt", 'w') as f:
            f.write(self.function_signature)

        self.logger.info("Function: {}".format(self.function_signature))
        self.logger.info("Create a new project: {}".format(self.new_project_name))
      
        # modify the dockerfile to copy the harness file, and set up the environment
        self.modify_dockerfile()

        # build the docker image
        build_cmd = self.oss_tool.get_script_cmd("build_image")
        # Try to build the image multiple times. Multiprocessing may cause the build to fail.
        build_res = False
        for _ in range(3):
            build_res = self.docker_tool.build_image(build_cmd)
            if build_res:
                break
        self.logger.info("Build Image a new project: {}, build res:{}".format(self.new_project_name, build_res))
        # failed to build the image
        assert build_res, "Failed to build the docker image for {}".format(self.new_project_name)

    def modify_dockerfile(self):
        '''Copy the harness file to overwrite all existing harness files'''
        # write copy in dockerfile and re-build the image
        project_path = self.benchcfg.oss_fuzz_dir /  "projects" / self.new_project_name
        with open(project_path / 'Dockerfile', 'a') as f:
            # Add additional statement in dockerfile to overwrite with generated fuzzer
            f.write(f'\nCOPY *.py  .\n')

            # wirte other commands
            f.write(f'\nRUN apt install -y clangd-18\n')
            # apt install bear
            f.write(f'\nRUN apt install -y bear\n')
            # install python library
            f.write('RUN pip install "tree-sitter-c<=0.23.4"\n')
            f.write('RUN pip install "tree-sitter-cpp<=0.23.4"\n')
            f.write('RUN pip install "tree-sitter-java<=0.23.4"\n')
            f.write('RUN pip install "tree-sitter<=0.24.0"\n')
            f.write('RUN pip install "multilspy==0.0.15"\n')
            f.write('RUN pip install "libclang==18.1.1"\n')

    def find_fuzzers(self) -> list[str]:
        '''Find all fuzzers in the project directory'''

        fuzz_path = self.benchcfg.cache_root / self.project_name / "fuzzer.txt"
        if not fuzz_path.exists():
            fuzz_path.parent.mkdir(parents=True, exist_ok=True)
            # cache the fuzzer name
            fuzzer_str = self.docker_tool.exec_in_container(self.code_retriever.container_id, "find /out/ -maxdepth 1 -type f")
            with open(fuzz_path, 'w') as f:
                f.write(fuzzer_str)
           

        fuzzer_str = fuzz_path.read_text()

        fuzzer_list:list[str] = []
        for fuzzer_path in fuzzer_str.splitlines():
            # skip the directory
            # if os.path.isdir(os.path.join(build_out_path, fuzzer_name)):
                # continue

            fuzzer_name = os.path.basename(fuzzer_path)

            if "." in fuzzer_name:
                continue
            if "llvm-symbolizer" in fuzzer_name:
                continue
            fuzzer_list.append(fuzzer_name)

        return fuzzer_list

    def find_harnesses(self, fuzzer_list:list[str]) -> dict[str, str]:
      
        # check whether the harness has corresponding harness file
        '''Get all files in the project directory'''

        harness_dict: dict[str, str] = {}
        # Execute `find` command to recursively list files and directories
        for fuzzer_name in fuzzer_list:
            find_cmd = f"find  /src/ -name {fuzzer_name}.* -type f" 

            results = self.docker_tool.run_cmd(find_cmd)

            if not results or results.startswith(DockerResults.Error.value):
                continue

            # split the directory structure
            all_file_path = results.splitlines()

            _temp_list: list[str] = []
            for file_path in all_file_path:
                file_path = Path(file_path.strip())
                name, ext = file_path.stem, file_path.suffix
                if ext in ALL_FILE_EXTENSION and name == fuzzer_name:
                    _temp_list.append(str(file_path))
                 
            if len(_temp_list) > 1:

                # choose the one with fuzz in file name
                fuzz_entry_list = [file_path for file_path in _temp_list if "fuzz" in file_path.lower()]
                if len(fuzz_entry_list) == 1:
                    _temp_list = fuzz_entry_list
                # skip this fuzzer
                else:
                    self.logger.error(f"Multiple harness files with fuzz entry function for {fuzzer_name}, {_temp_list}, skip this fuzzer")

            # only one harness file
            if len(_temp_list) == 1:
                harness_dict[fuzzer_name] = _temp_list[0]

        return harness_dict

    def cache_harness_fuzzer_pairs(self)-> dict[str, Path]:
        '''Cache the harness and fuzzer pairs'''

        def to_path(harness_fuzzer_dict: dict[str, str]) -> dict[str, Path]:
            '''Convert the harness_fuzzer_dict to the correct type'''
                    # translate dict to the correct type
            new_harness_fuzzer_dict: dict[str, Path] = {}
            for key, value in harness_fuzzer_dict.items():
                new_harness_fuzzer_dict[key] = Path(value)
            return new_harness_fuzzer_dict
        

        json_path = self.benchcfg.cache_root / self.project_name / "harness_fuzzer_pairs.json"
        if json_path.exists():
            with open(json_path, 'r') as f:
                harness_fuzzer_dict = json.load(f)
                self.logger.info(f"Using Cached harness_fuzzer_pairs.json, content:{harness_fuzzer_dict}")
                return to_path(harness_fuzzer_dict)

        fuzzer_list = self.find_fuzzers()
        harness_fuzzer_dict = self.find_harnesses(fuzzer_list)

        # save the harness pairs
        with open(json_path, 'w') as f:
            json.dump(harness_fuzzer_dict, f)

        return to_path(harness_fuzzer_dict)

    def clean_workspace(self):
        '''Clean the workspace'''
        try:        
            self.code_retriever.remove_container()

            # first remove the out directory
            self.docker_tool.clean_build_dir()
            
            # remove the docker image here
            self.docker_tool.remove_image()
            self.logger.info("remove the docker image for {}".format(self.new_project_name))
            # remove the project directory
            shutil.rmtree(self.benchcfg.oss_fuzz_dir / "projects" / self.new_project_name)

            # clean the build directory
            shutil.rmtree(self.benchcfg.oss_fuzz_dir / "build" / "out"/ self.new_project_name)
            # remove the corpus, needs root permission
            # corpus_dir = os.path.join(self.save_dir, "corpora")
            # if os.path.exists(corpus_dir):
                # shutil.rmtree(corpus_dir)
        except:
            pass


