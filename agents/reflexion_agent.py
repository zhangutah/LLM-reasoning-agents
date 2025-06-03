from typing import Annotated
import subprocess as sp
from langchain_openai import ChatOpenAI
from langchain_core.language_models import BaseChatModel
from langchain_core.messages import BaseMessage
from typing_extensions import TypedDict
from langgraph.graph.message import add_messages # type: ignore
import os
from langgraph.checkpoint.memory import MemorySaver
from langgraph.graph import StateGraph, START, END # type: ignore
import random
from constants import LanguageType, CompileResults, PROJECT_PATH
from pydantic import BaseModel, Field
import re
import logging
import shutil
from typing import Callable, Any
from langchain_core.tools import  StructuredTool
from langgraph.prebuilt import ToolNode # type: ignore
import json
from tools.fuzz_tools.log_parser import CompileErrorExtractor
from prompts.raw_prompts import CODE_FIX_PROMPT, EXTRACT_CODE_PROMPT, FUZZ_FIX_PROMPT, INIT_PROMPT
from utils.misc import save_code_to_file, extract_name
from tools.fuzz_tools.compiler import Compiler
from tools.code_retriever import CodeRetriever
from tools.fuzz_tools.run_fuzzer import FuzzerRunner
from constants import FuzzResult,  ALL_FILE_EXTENSION, DockerResults
from utils.oss_fuzz_utils import OSSFuzzUtils
from utils.docker_utils import DockerUtils
from pathlib import Path
from docker.models.containers import Container

class CodeAnswerStruct(BaseModel):
    """Split the answer into the content before the code, the code, and the content after the code."""
    before_code: str = Field(description="The unnecessary explanation before the code.")
    source_code: str = Field(description="The code part of the answer.")
    after_code: str = Field(description="The unnecessary explanation after the code.")


class CodeFormatTool():

    def __init__(self, llm: BaseChatModel, prompt: str):
        self.llm = llm
        self.prompt = prompt

    def extract_code(self, response: str) -> str:
        '''Extract the code from the response with LLM'''

        extract_prompt = self.prompt.format(response=response)

        _respsone: CodeAnswerStruct = self.llm.invoke(extract_prompt) # type: ignore
        source_code = _respsone.source_code 
        # deal with the new line
        # if "\\n" in source_code:
            # source_code = source_code.replace("\\n", "\n")

        # remove the line number if exists
        source_code = re.sub(r'^//\s+\d+:\s?', '', source_code, flags=re.MULTILINE)
        # remove some useless string
        source_code = source_code.replace("```cpp", "")
        source_code = source_code.replace("```", "")
        # if source_code and source_code.startswith("c\n"):
            # source_code = source_code[1:]
        return source_code

class InitGenerator:
    def __init__(self, runnable: BaseChatModel, max_tool_call: int, continue_flag: bool, 
                 save_dir: Path, code_callback: Callable[[str], str], logger: logging.Logger):

        self.runnable = runnable
        self.save_dir = save_dir
        self.code_callback = code_callback
        self.logger = logger
        self.tool_history = []
        self.max_tool_call = max_tool_call
        self.continue_flag = continue_flag
        self.count_tool_call = 0 # count the number of tool calls

    def respond(self, state: dict[str, Any]) -> dict[str, Any]:
        # prompt is in the messages
        response = self.runnable.invoke(state["messages"])

        # check if call the tool
        if len(response.tool_calls) != 0: # type: ignore
            self.count_tool_call += 1
            
            # check if the tool call is too much
            if self.count_tool_call > self.max_tool_call:
                return {"messages": f"{END}. Initial Generator exceeds max tool call {self.max_tool_call}"}
            else:
                return {"messages": response}

        # not call the tool, the response is the code. format the code
        source_code = self.code_callback(response.content) # type: ignore

        # add prompt to the messages
        if self.continue_flag:
            full_source_code = state["messages"][0].content + source_code
        else:
            full_source_code = source_code

        # save source code to file
        save_code_to_file(full_source_code,  self.save_dir / "draft_fix0.txt")

        self.logger.info(f"Generate Draft Code.")
        return {"messages": ("assistant", source_code), "harness_code": full_source_code, "fix_counter": 0}


class CompilerWraper(Compiler):
    def __init__(self, oss_fuzz_dir: Path, project_name: str, new_project_name: str,
                     project_lang: LanguageType, harness_dict:dict[str, Path], 
                     save_dir: Path, logger: logging.Logger):
        super().__init__(oss_fuzz_dir, project_name, new_project_name)
        self.logger = logger
        self.project_lang = project_lang
        self.save_dir = save_dir
        self.harness_dict = harness_dict
        self.start_index = 0

    def extract_error_msg(self, all_msg: str) -> str:
        '''
        Extract the error message from the raw message. 
        If you wanna customize the error message extraction, you can override this function.
        '''
        all_errors = CompileErrorExtractor(self.project_lang).extract_error_message(all_msg)
        return "\n".join(all_errors)
        # final_msg: list[str] = []
    
#         for i, error_lines in enumerate(all_errors):
#             prefix = f"The {i}th error:\n"
#             error_msg = "\n".join(error_lines)
#             final_msg.append( prefix + error_msg)
#             # why the following code
#             # for line in error_lines:
#                 # if "error:" in line:
#                     # final_msg.append(line)
# # 
#         return "\n".join(final_msg)

    # TODO change this
    def _match_link_pattern(self, harness_file_name:str, pattern:str,  error_msg: str) -> bool:
           # Find all matches
        matches = re.findall(pattern, error_msg)

        # Print the results
        for file_name, _ in matches:
            # print(f"Error in file {file_name} for function {function_name}")
            if file_name.strip() != harness_file_name.strip():
                return True
        return False

    def is_link_error(self, error_msg: str, harness_path: Path) -> bool:
        '''Check if the error message is a link error'''

        harness_file_name = harness_path.name
        # 
        link_error_pattern = r"DWARF error: invalid or unhandled FORM value: 0x25"
        if link_error_pattern not in error_msg:
            return False
        
        # Regular expression to match the errored file and undefined function
        # TODO only test on C Projects
        undefined_pattern = r"([\w\-]+\.(?:c|o)):.*undefined reference to `([^']+)'"
        multi_definiation_pattern = r"([\w\-]+\.(?:c|o)):.*multiple definition of `([^']+)'"
        # Find all matches
        for pattern in [undefined_pattern, multi_definiation_pattern]:
            if self._match_link_pattern(harness_file_name, pattern, error_msg):
                return True
     
        return False

    def compile(self, state: dict[str, Any]) -> dict[str, Any]: # type: ignore
        '''Compile the harness file'''
        
        fix_count = state.get("fix_counter", 0)

        # save the harness code to current output directory
        save_code_to_file(state["harness_code"], self.save_dir / "harness.txt")
      
        # if self.counter > self.max_compile:
            # log_if_exists(self.logger, f"Max compile times reached for {self.new_project_name}:{self.project_harness_name}", logger_level=logging.INFO)
            # return {"messages": END}

        for i, (fuzzer_name, harness_path) in enumerate(self.harness_dict.items()):
            
            if i < self.start_index:
                continue

            self.logger.info(f'Compile Start for draft_fix{fix_count} using {fuzzer_name}.')
            # compile the harness file
            compile_res, all_msg = super().compile(state["harness_code"], harness_path, fuzzer_name)

            self.logger.info(f'Compile End for draft_fix{fix_count} using {fuzzer_name}. Res: {compile_res}')

            save_code_to_file(all_msg, self.save_dir / f"build_{fix_count}.log")

            # Project realted error, No need to continue
            if compile_res in [CompileResults.ImageError, CompileResults.FuzzerError]:
                return {"messages": ("user", END + compile_res.value)}
            
            # compile error
            elif compile_res == CompileResults.CodeError:
                # extract error msg
                error_msg = self.extract_error_msg(all_msg)
                save_code_to_file(error_msg, self.save_dir / f"build_error_{fix_count}.log")

                if not self.is_link_error(error_msg, harness_path):
                    # save raw error message
                    return {"messages": ("user", compile_res.value), "build_msg": error_msg, "fuzzer_name": fuzzer_name, "fuzzer_path": harness_path}
                else:
                    # link error, try next harness
                    self.logger.error(f"Link Error for draft_fix{fix_count} using {fuzzer_name}, Now try another harness file.")
                    self.start_index = i+1

            # compile success
            else:
                return {"messages": ("user", compile_res.value), "build_msg": all_msg, "fuzzer_name": fuzzer_name, "fuzzer_path": harness_path}
                
        return {"messages": ("user", END + "Link Error, tried all harness")}

class FixerPromptBuilder:
    def __init__(self, compile_fix_prompt: str, fuzz_fix_prompt: str, project_lang: LanguageType, clear_msg_flag: bool):
        self.compile_fix_prompt = compile_fix_prompt
        self.fuzz_fix_prompt = fuzz_fix_prompt
        self.project_lang = project_lang
        self.clear_msg_flag = clear_msg_flag

    def build_compile_prompt(self, harness_code: str, error_msg: str):
        '''
        Build the prompt for the code fixer. If you need to customize the prompt, you can override this function.
        '''
        return self.compile_fix_prompt.format(harness_code=harness_code, error_msg=error_msg, project_lang=self.project_lang)

    def build_fuzz_prompt(self, harness_code: str, error_msg:str):
        '''
        Build the prompt for the code fixer. If you need to customize the prompt, you can override this function.
        '''
        return self.fuzz_fix_prompt.format(harness_code=harness_code, error_msg=error_msg, project_lang=self.project_lang)

    def respond(self, state: dict[str, Any]) -> dict[str, Any]:
        fix_counter = state.get("fix_counter", 0)
        last_message = state["messages"][-1].content
        if fix_counter == 0 or self.clear_msg_flag:
            # clear previous messages, need to build the fix prompt based on the provided template 
            state["messages"].clear()
            if last_message.startswith(CompileResults.CodeError.value):
                fix_prompt = self.build_compile_prompt(state["harness_code"], state["build_msg"])
            else:
                fix_prompt = self.build_fuzz_prompt(state["harness_code"], state["fuzz_msg"])
        else:
            # keep the previous messages, just add the error message
            if last_message.startswith(CompileResults.CodeError.value):
                fix_prompt = "Complie Error Messages:\n" + state["build_msg"]
            else:
                fix_prompt = "Fuzz Error Messages:\n" + state["fuzz_msg"]

        return {"messages": ("user", fix_prompt)}

class CodeFixer:
    def __init__(self, runnable: BaseChatModel, max_fix: int, max_tool_call: int, save_dir: Path, 
                    cache_dir: Path, code_callback:Callable[[str], str] , logger:logging.Logger):

        self.runnable = runnable
        self.save_dir = save_dir
        self.cache_dir = cache_dir

        self.code_callback = code_callback
        self.logger = logger
        self.max_tool_call = max_tool_call
        self.max_fix = max_fix
        self.tool_call_counter = 0

    def respond(self, state: dict[str, Any]) -> dict[str, Any]:
        fix_counter = state.get("fix_counter", 0)
        self.logger.info(f"Fix start for draft_fix{fix_counter}.")
   
        response: BaseMessage = self.runnable.invoke(state["messages"])

        # check if call the tool
        if len(response.tool_calls) != 0: # type: ignore
            self.tool_call_counter += 1

            if self.tool_call_counter > self.max_tool_call:
                self.logger.info(f"Max tool call times ({self.max_tool_call}) reached.")
                return {"messages": f"{END}. Max tool call times ({self.max_tool_call}) reached."}

            # tool call response
            return {"messages": response}
       
        # not call the tool, the response is the code. 
        self.logger.info(f"Fix end for draft_fix{fix_counter}.")

        fix_counter = fix_counter+1
        if fix_counter > self.max_fix:
            self.logger.info(f"Max fix time ({self.max_fix}) reached")
            return {"messages": f"{END}. Max fix times ({self.max_fix}) reached", "fix_counter": fix_counter}
        
        # extract the code
        source_code = self.code_callback(response.content) # type: ignore

        new_save_name = "draft_fix{}.txt".format(fix_counter)
        save_code_to_file(source_code, self.save_dir / new_save_name)
        # update the harness code
        return {"messages": ("assistant", source_code), "harness_code": source_code, "fix_counter": fix_counter}


class FuzzerWraper(FuzzerRunner):
    def __init__(self, oss_fuzz_dir: Path, new_project_name: str,
                 project_lang: LanguageType, run_timeout: int , 
                 save_dir: Path, logger: logging.Logger):

        super().__init__(oss_fuzz_dir, new_project_name, project_lang, run_timeout, save_dir)
        self.logger = logger

        
    def run_fuzzing(self, state: dict[str, Any]) -> dict[str, Any]: # type: ignore
        fix_counter = state.get("fix_counter", 0)
        fuzzer_name = state.get("fuzzer_name", "")

        self.logger.info(f"Run {fix_counter}th Fuzzer for {self.new_project_name}:{fuzzer_name}")
       
        fuzz_res, error_type_line, stack_list = super().run_fuzzing(fix_counter, fuzzer_name)
        
        self.logger.info(f"Fuzz res:{fuzz_res}, {error_type_line} for {self.new_project_name}:{fuzzer_name}")
    
        # unable to fix the code
        if fuzz_res == FuzzResult.RunError:
            return {"messages": ("user", END + "Run Error")}
        elif fuzz_res == FuzzResult.ConstantCoverageError:
            return {"messages": ("user", fuzz_res), "fuzz_msg": "The above code can be built successfully but its fuzzing seems not effective since the coverage never change. Please make sure the fuzz data is used."}
        elif fuzz_res == FuzzResult.ReadLogError:
            return {"messages": ("user", fuzz_res), "fuzz_msg": '''The above code can be built successfully but it generates a extreme large log which indicates the fuzz driver may include some bugs. 
                                                                    Please do not print any information. '''}
        elif fuzz_res == FuzzResult.LackCovError:
            return {"messages": ("user", fuzz_res), "fuzz_msg": '''The above code can be built successfully but its fuzzing seems not effective since it lack the initial or final code coverage info.
                                                                    Please make sure the fuzz data is used.'''}
        elif fuzz_res == FuzzResult.Crash:
            # extract the first error message
            error_type = error_type_line[0] if len(error_type_line) > 0 else "Unknown Crash, Unable to extract the error message"
            first_stack = stack_list[0] if len(stack_list) > 0 else ["Unknown Crash, Unable to extract the stack trace"]
            fuzz_error_msg = error_type + "\n" + "\n".join(first_stack)
            return {"messages": ("user", fuzz_res), "fuzz_msg": fuzz_error_msg}
        else:
            return {"messages": ("user", fuzz_res)}


class FuzzState(TypedDict):
    messages: Annotated[list[str], add_messages]
    harness_code: str
    build_msg: str
    fuzz_msg: str
    fix_counter: int
    fuzzer_name: str
    fuzzer_path: Path


class AgentFuzzer():

    # Constants
    HarnessGeneratorNode = "HarnessGenerator"
    CompilerNode = "Compiler"
    CodeFixerNode = "CodeFixer"
    FixerToolNode = "FixerTools"
    GenerationToolNode = "GenerationTools"
    FuzzerNode = "Fuzzer"
    FixBuilderNode = "FixBuilder"

    def __init__(self, n_examples: int, example_mode: str, model_name: str, temperature: float, oss_fuzz_dir: Path, project_name: str, function_signature: str,
                 usage_token_limit: int, model_token_limit: int, run_time: int, max_fix: int, max_tool_call: int,  
                 clear_msg_flag: bool, save_dir: Path, cache_dir: Path, n_run: int = 1):
        
        self.n_examples = n_examples
        self.example_mode = example_mode
        self.eailier_stop_flag = False
        self.n_run = n_run
        self.model_name = model_name
        self.temperature = temperature
        self.oss_fuzz_dir = oss_fuzz_dir
        self.project_name = project_name
        self.function_signature = function_signature
        self.usage_token_limit = usage_token_limit
        self.model_token_limit = model_token_limit
        self.run_time = run_time
        self.max_fix = max_fix
        self.max_tool_call = max_tool_call
        self.cache_dir = cache_dir
        self.clear_msg_flag = clear_msg_flag

        # random generate a string for new project name
        random_str = ''.join(random.choices("abcdefghijklmnopqrstuvwxyz", k=16))
        self.new_project_name = f"run{self.n_run}_{random_str}"
        function_name = extract_name(function_signature)
        self.save_dir = save_dir / project_name.lower() / function_name.lower() / self.new_project_name
        self.logger = self.setup_logging()

        self.oss_tool = OSSFuzzUtils(self.oss_fuzz_dir, self.project_name, self.new_project_name)
        self.project_lang = self.oss_tool.get_project_language()

        self.docker_tool = DockerUtils(self.oss_fuzz_dir, self.project_name, self.new_project_name, self.project_lang)
        init_flag = self.init_workspace()

        if not init_flag:
            self.eailier_stop_flag = True
            self.logger.error("Failed to initialize the workspace. Return")
            return 
        
        # collect all harness_path, fuzzer pairs
        _fuzzer_name,  _harness_path = self.oss_tool.get_harness_and_fuzzer()
        self.harness_pairs = self.cahche_harness_fuzzer_pairs()
        if  _fuzzer_name not in self.harness_pairs:
            self.harness_pairs[_fuzzer_name] = _harness_path
        else:
            # move the harness file to the first
            self.harness_pairs = {_fuzzer_name: _harness_path, **self.harness_pairs}

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

        dst_path = self.oss_fuzz_dir / "projects" / self.new_project_name
        if dst_path.exists():
            # clean the directory
            shutil.rmtree(dst_path)

        # copy a backup of the project
        scr_path = self.oss_fuzz_dir / "projects" / self.project_name
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
        build_res = self.docker_tool.build_image(build_cmd)
        self.logger.info("Build Image a new project: {}, build res:{}".format(self.new_project_name, build_res))
        
        # failed to build the image
        if not build_res:
            return False

        # cache the compile_commands.json
        if self.project_lang in [LanguageType.C, LanguageType.CPP]:
            compile_res = self.cache_compile_commands()
            if not compile_res:
                return False
        
        return True


    def modify_dockerfile(self):
        '''Copy the harness file to overwrite all existing harness files'''
        # write copy in dockerfile and re-build the image
        project_path = self.oss_fuzz_dir /  "projects" / self.new_project_name
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


    def cache_compile_commands(self) -> bool:
        
        cmd_json_name = "compile_commands.json"
        json_path = self.cache_dir / self.project_name / cmd_json_name
        if json_path.exists():
            self.logger.info(f"Using Cached compile_commands.json")
            return True
       
        # make the cache directory
        dir_path = self.cache_dir / self.project_name
        dir_path.mkdir(parents=True, exist_ok=True)

        # run bear to generate compile_commands.json
        def bear_call_back(container: Container) -> str:
            res = self.docker_tool.contrainer_exec_run(container, "bear compile")
            self.logger.info(f"bear res: {res.splitlines()[-2:]}")

            container_info = container.attrs 
            workdir = container_info['Config'].get('WorkingDir', '')
          
            try:
                sp.run(f"docker cp {container.id}:{os.path.join(workdir, cmd_json_name)}  {dir_path}", shell=True, check=True, stdout=sp.PIPE, stderr=sp.PIPE)
            except Exception as e:
                self.logger.error(f"docker copy error: {e}")
                return DockerResults.Error.value

            # cache the fuzzer name
            fuzzer_str = self.docker_tool.contrainer_exec_run(container, "find /out/ -maxdepth 1 -type f")
            with open(self.cache_dir / self.project_name / "fuzzer.txt", 'w') as f:
                f.write(fuzzer_str)

            return DockerResults.Success.value

        compile_cmd = self.docker_tool.run_call_back(bear_call_back)
        if compile_cmd.startswith(DockerResults.Error.value):
            self.logger.error(f"No {cmd_json_name} , Exit")
            return False    
        return True

    def find_fuzzers(self) -> list[str]:
        '''Find all fuzzers in the project directory'''
          
        fuzz_path = self.cache_dir / self.project_name / "fuzzer.txt"
        if not fuzz_path.exists():
            return []

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
                file_path = file_path.strip()
                file_name = os.path.basename(file_path)
                name, ext = file_name.split(".")
                if ext in ALL_FILE_EXTENSION and name == fuzzer_name:
                    _temp_list.append(file_path)
                 
            if len(_temp_list) > 1:
                self.logger.info(f"Multiple harness files for {fuzzer_name}, {_temp_list}")
                continue
            
            # only one harness file
            if len(_temp_list) == 1:
                harness_dict[fuzzer_name] = _temp_list[0]

        return harness_dict

    def cahche_harness_fuzzer_pairs(self)-> dict[str, Path]:
        '''Cache the harness and fuzzer pairs'''

        def to_path(harness_fuzzer_dict: dict[str, str]) -> dict[str, Path]:
            '''Convert the harness_fuzzer_dict to the correct type'''
                    # translate dict to the correct type
            new_harness_fuzzer_dict: dict[str, Path] = {}
            for key, value in harness_fuzzer_dict.items():
                new_harness_fuzzer_dict[key] = Path(value)
            return new_harness_fuzzer_dict
        

        json_path = self.cache_dir / self.project_name / "harness_fuzzer_pairs.json"
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

        self.logger.info(f"Create harness_fuzzer_pairs.json, content:{harness_fuzzer_dict}")

        return to_path(harness_fuzzer_dict)

    def clean_workspace(self):
        '''Clean the workspace'''
        try:        

            # first remove the out directory
            self.docker_tool.clean_build_dir()
            
            # remove the docker image here
            self.docker_tool.remove_image()
            self.logger.info("remove the docker image for {}".format(self.new_project_name))
            # remove the project directory
            shutil.rmtree(self.oss_fuzz_dir / "projects" / self.new_project_name)

            # clean the build directory
            shutil.rmtree(self.oss_fuzz_dir / "build" / "out"/ self.new_project_name)

            # remove the corpus, needs root permission
            # corpus_dir = os.path.join(self.save_dir, "corpora")
            # if os.path.exists(corpus_dir):
                # shutil.rmtree(corpus_dir)
        except:
            pass


    def compile_router_mapping(self, state: dict[str, Any]) -> str:
        last_message = state["messages"][-1]

        # print(messages)
        if last_message.content == CompileResults.Success.value:
            return self.FuzzerNode
        elif last_message.content == CompileResults.CodeError.value:
            return self.FixBuilderNode
        else:
            return END

    def code_fixer_mapping(self, state:dict[str, Any]) -> str:
        last_message = state["messages"][-1]

        if last_message.content.startswith(END):
            return END
        # call tools
        if len(last_message.tool_calls) != 0:
            return self.FixerToolNode
        else:
            return self.CompilerNode

    def generator_mapping(self, state: dict[str, Any]) -> str:
        last_message = state["messages"][-1]

        if last_message.content.startswith(END):
            return END

        # call tools
        if len(last_message.tool_calls) != 0:
            return self.GenerationToolNode
        else:
            return self.CompilerNode
        
    def fuzzer_router_mapping(self, state: dict[str, Any]) -> str:
        last_message = state["messages"][-1]

        # print(messages)
        if last_message.content.startswith(FuzzResult.NoError.value):
            return END
        elif last_message.content.startswith(END):
            return END
        else:
            return self.FixBuilderNode

    def build_graph(self)-> StateGraph:

        llm = ChatOpenAI(model=self.model_name)
        code_retriever = CodeRetriever(self.oss_fuzz_dir, self.project_name, self.new_project_name, self.project_lang, self.cache_dir, self.logger)
       
        # decide whether to use the tool according to the header_desc_mode    
   
        header_tool = StructuredTool.from_function( # type: ignore
                func=code_retriever.get_symbol_header,
                name="get_symbol_header",
                description=code_retriever.get_symbol_header.__doc__,
            )
        definition_tool = StructuredTool.from_function( # type: ignore
                func=code_retriever.get_symbol_definition,
                name="get_symbol_definition",
                description=code_retriever.get_symbol_definition.__doc__,
            )
        reference_tool = StructuredTool.from_function( # type: ignore
                func=code_retriever.get_symbol_references,
                name="get_symbol_references",
                description=code_retriever.get_symbol_references.__doc__,
            )

        tools = [header_tool, definition_tool, reference_tool]

        if tools:
            # bind the tools, the tools may be empty
            llm_init_generator: BaseChatModel = llm.bind_tools(tools) # type: ignore
            llm_code_fixer: BaseChatModel = llm.bind_tools(tools) # type: ignore
        else:
            llm_code_fixer = llm
            llm_init_generator = llm

        # code formatter
        llm_code_extract: BaseChatModel = llm.with_structured_output(CodeAnswerStruct) # type: ignore
        code_formater = CodeFormatTool(llm_code_extract, EXTRACT_CODE_PROMPT)

        draft_responder = InitGenerator(llm_init_generator, self.max_tool_call, continue_flag=False, save_dir=self.save_dir, 
                                        code_callback=code_formater.extract_code, logger=self.logger)

        #  compile_fix_prompt: str, fuzz_fix_prompt: str, clear_msg_flag: bool)
        fix_builder = FixerPromptBuilder(CODE_FIX_PROMPT, FUZZ_FIX_PROMPT, self.project_lang, self.clear_msg_flag)

        # code fixer needs old project name
        code_fixer = CodeFixer(llm_code_fixer, self.max_fix, self.max_tool_call,  self.save_dir, self.cache_dir,
                                 code_callback=code_formater.extract_code, logger=self.logger)

        fuzzer = FuzzerWraper(self.oss_fuzz_dir, self.new_project_name, self.project_lang, 
                             self.run_time,  self.save_dir,  self.logger)
        
        compiler = CompilerWraper(self.oss_fuzz_dir, self.project_name, self.new_project_name, 
                                  self.project_lang, self.harness_pairs, self.save_dir, self.logger)

        # build the graph
        builder = StateGraph(FuzzState)
        memory = MemorySaver()

        # add nodes
        tool_node = ToolNode(tools)

        builder.add_node(self.HarnessGeneratorNode, draft_responder.respond) # type: ignore
        builder.add_node(self.CompilerNode, compiler.compile) # type: ignore
        builder.add_node(self.FixBuilderNode, fix_builder.respond) # type: ignore
        builder.add_node(self.CodeFixerNode, code_fixer.respond) # type: ignore
        builder.add_node(self.FixerToolNode, tool_node) # type: ignore
        builder.add_node(self.GenerationToolNode, tool_node) # type: ignore
        builder.add_node(self.FuzzerNode, fuzzer.run_fuzzing)  # type: ignore

        # add edges
        builder.add_edge(START, self.HarnessGeneratorNode)
        builder.add_edge(self.FixerToolNode, self.CodeFixerNode)
        builder.add_edge(self.FixBuilderNode, self.CodeFixerNode)
        builder.add_edge(self.GenerationToolNode, self.HarnessGeneratorNode)

        # add conditional edges
        builder.add_conditional_edges(self.CompilerNode, self.compile_router_mapping,  [self.FixBuilderNode, self.FuzzerNode, END])
        builder.add_conditional_edges(self.CodeFixerNode, self.code_fixer_mapping,  [self.CompilerNode, self.FixerToolNode, END])
        builder.add_conditional_edges(self.HarnessGeneratorNode, self.generator_mapping,  [self.CompilerNode, self.GenerationToolNode, END])
        builder.add_conditional_edges(self.FuzzerNode, self.fuzzer_router_mapping, [self.FixBuilderNode, END])

        # the path map is mandatory
        graph: StateGraph = builder.compile(memory)  # type: ignore
        return graph

    def run_graph(self, graph: StateGraph):
        if self.eailier_stop_flag:
            return
        
        prompt = INIT_PROMPT.format(project_lang=self.project_lang,
                                    project_name=self.project_name,
                                    function_usage="",
                                    function_document="",
                                    function_signature=self.function_signature,
                                    function_name=self.function_signature)
        # plot_graph(graph)
        config = {"configurable": {"thread_id": "1"}}
        events = graph.stream( # type: ignore
            {"messages": [("user", prompt)]},
            config,
            stream_mode="values",
        )

        for i, step in enumerate(events): # type: ignore
            print(f"Step {i}")
            step["messages"][-1].pretty_print() # type: ignore
            # pass


# def process_project(llm_name, ossfuzz_dir, project_name, function_name, save_dir, run_time, max_iterations, header_desc_mode):

#     agent_fuzzer = AgentFuzzer(llm_name, ossfuzz_dir, project_name, function_name, save_dir, run_time, max_iterations, header_desc_mode)
#     atexit.register(agent_fuzzer.clean_workspace)

#     try:
#         # Your main logic here
#         agent_fuzzer.build_graph()
#         agent_fuzzer.run_graph()

#     except Exception as e:
#         print(e)
#         print("Program interrupted.")
#     finally:
#         agent_fuzzer.clean_workspace()

# def run_parallel():
#     # build graph
#     ossfuzz_dir = "/home/yk/code/oss-fuzz/"
#     # absolute path
#     save_dir = os.path.join(PROJECT_PATH, "outputs")
#     llm_name = "gpt-4o"
#     run_time = 6
#     max_iterations = 5
#     header_desc_mode = "detailed"
#     function_list = []
#     # read benchmark names
#     bench_dir = os.path.join(PROJECT_PATH, "benchmark-sets", "all")
#     all_files = os.listdir(bench_dir)

#     # sort 
#     all_files.sort()

#     # resume from project name
#     # resume_project_name = "njs.yaml"
#     # index = all_files.index(resume_project_name)
#     index = 0

#     for file in all_files[index:]:
#         # read yaml file
#         with open(os.path.join(bench_dir, file), 'r') as f:
#             data = yaml.safe_load(f)
#             project_name = data.get("project")
#             lang_name = data.get("language")
#             project_harness = data.get("target_path")

#             if project_name != "tinyxml2":
#                 continue

#             if lang_name not in ["c++", "c"]:
#                 continue
        
#             for function in data.get("functions"):
#                 function_list.append((project_name, function["signature"]))

#     print("total projects:", len(function_list))
#     with Pool(processes=os.cpu_count()//2) as pool:

#         for project_name, function_name in function_list:
#             # pool.apply(process_project, args=(llm_name, ossfuzz_dir, project_name,function_name, save_dir, run_time, max_iterations))
#             pool.apply_async(process_project, args=(llm_name, ossfuzz_dir, project_name, function_name, save_dir, run_time, max_iterations, header_desc_mode))

#         pool.close()
#         pool.join()



# if __name__ == "__main__":

#     # run_parallel()
#     # exit()
    
#     # build graph
#     ossfuzz_dir = Path("/home/yk/code/oss-fuzz/")
#     project_name = "coturn"

#     # absolute path
#     save_dir = Path("/home/yk/code/LLM-reasoning-agents/outputs/agent/apr17/")
#     cache_dir = Path("/home/yk/code/LLM-reasoning-agents/cache/")
#     llm_name = "gpt-4o"
#     # function_sig = r"void tinyxml2::XMLElement::SetAttribute(const char *, const char *)"
#     function_sig = r"bool stun_is_response(const stun_buffer *)"
#     # function_sig = "cJSON * cJSON_Parse(const char *)"

#     agent_fuzzer = AgentFuzzer(1, "rank", llm_name, 0.7, ossfuzz_dir, project_name, function_sig, 
#                                usage_token_limit=1000, model_token_limit=8096,
#                                run_time=1, max_fix=5, max_tool_call=30, clear_msg_flag=True, 
#                                save_dir=save_dir, cache_dir=cache_dir)
    
#     atexit.register(agent_fuzzer.clean_workspace)

#     graph = agent_fuzzer.build_graph()
#     agent_fuzzer.run_graph(graph)
    