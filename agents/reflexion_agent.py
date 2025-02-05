from typing import Annotated
import subprocess as sp
from langchain_core.messages import ToolMessage
from langchain_openai import ChatOpenAI
from typing_extensions import TypedDict
from langgraph.graph.message import add_messages, MessagesState
import os
from langgraph.checkpoint.memory import MemorySaver
from langgraph.graph import END, StateGraph, START
import io
import random
import getpass
from constants import LanguageType, CompileResults, PROJECT_PATH
from pydantic import BaseModel, Field
import re
import logging
import shutil
from typing import Callable, Optional
from langchain_core.tools import tool, BaseTool, StructuredTool
from langgraph.prebuilt import ToolNode
from langchain_anthropic import ChatAnthropic
from multiprocessing import Pool, Lock, Manager
import yaml
import atexit
import asyncio
import json
from tools.log_parser import CompileErrorExtractor
from prompts.raw_prompts import CODE_FIX_PROMPT, EXTRACT_CODE_PROMPT, CODE_FIX_PROMPT_TOOLS
from utils.misc import plot_graph, load_pormpt_template, save_code_to_file
from tools.compiler import Compiler
from tools.code_retriever import CodeRetriever, header_desc_mapping
from tools.run_fuzzer import FuzzerRunner
from constants import FuzzResult, ToolDescMode, LSPFunction
from utils.oss_fuzz_utils import OSSFuzzUtils
from utils.docker_utils import DockerUtils

class CodeAnswerStruct(BaseModel):
    """Split the answer into the content before the code, the code, and the content after the code."""
    before_code: str = Field(description="The unnecessary explanation before the code.")
    source_code: str = Field(description="The code part of the answer.")
    after_code: str = Field(description="The unnecessary explanation after the code.")


class CodeFormatTool():

    def __init__(self, llm, prompt):
        self.llm = llm
        self.prompt = prompt

    def extract_code(self, response):
        '''Extract the code from the response with LLM'''

        extract_prompt = self.prompt.format(response=response)

        source_code = self.llm.invoke(extract_prompt).source_code
        # deal with the new line
        if "\\n" in source_code:
            source_code = source_code.replace("\\n", "\n")

        # remove some useless string
        source_code = source_code.replace("```cpp", "")
        source_code = source_code.replace("```", "")
        if source_code and  source_code.startswith("c"):
            source_code = source_code[1:]
        return source_code

class InitGenerator:
    def __init__(self, runnable, max_tool_call: int, continue_flag: bool, save_dir: str, code_callback=None, logger=None):

        self.runnable = runnable
        self.save_dir = save_dir
        self.code_callback = code_callback
        self.logger = logger
        self.tool_history = []
        self.max_tool_call = max_tool_call
        self.continue_flag = continue_flag
        self.count_tool_call = 0 # count the number of tool calls

    def respond(self, state: dict):
        # prompt is in the messages
        response = self.runnable.invoke(state["messages"])

        # check if call the tool
        if len(response.tool_calls) != 0:
            self.count_tool_call += 1
            
            # check if the tool call is too much
            if self.count_tool_call > self.max_tool_call:
                return {"messages": END}
            else:
                return {"messages": response}

        # not call the tool, the response is the code. format the code
        source_code = self.code_callback(response.content)

        # add prompt to the messages
        if self.continue_flag:
            source_code = state["messages"][0].content + source_code

        # save source code to file
        save_path = os.path.join(self.save_dir, "draft.txt")
        save_code_to_file(source_code, save_path)

        self.logger.info(f"Generate Draft Code.")
        return {"messages": [("assistant", source_code)], "harness_code": source_code}


class CompilerWraper(Compiler):
    def __init__(self, oss_fuzz_dir: str, project_name: str, new_project_name: str, project_lang: str, save_dir: str, logger=None):
        super().__init__(oss_fuzz_dir, project_name, new_project_name)
        self.logger = logger
        self.project_lang = project_lang
        self.save_dir = save_dir
        self.counter = 0


    def extract_error_msg(self, all_msg: str) -> str:
        '''
        Extract the error message from the raw message. 
        If you wanna customize the error message extraction, you can override this function.
        '''
        final_msg = []
        all_errors = CompileErrorExtractor(self.project_lang).extract_error_message(all_msg)
        return "\n".join(all_errors)
    
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

    def compile_harness(self, state: dict):
        '''Compile the harness file'''
        self.counter += 1
        
        self.logger.info(f'Start {self.counter}th Compile for {self.new_project_name}:{os.path.basename(self.project_harness_path)}')

        # save the harness code to current output directory
        save_code_to_file(state["harness_code"], os.path.join(self.save_dir, f"harness.txt"))
      
        # if self.counter > self.max_compile:
            # log_if_exists(self.logger, f"Max compile times reached for {self.new_project_name}:{self.project_harness_name}", logger_level=logging.INFO)
            # return {"messages": END}
        
        # compile the harness file
        compile_res, all_msg = super().compile_harness(state["harness_code"])

        self.logger.info(f'Compile Res: {compile_res} for {self.new_project_name}:{os.path.basename(self.project_harness_path)}')

        # Project realted error, No need to continue
        if compile_res in [CompileResults.ImageError, CompileResults.FuzzerError]:
            return {"messages": END}
        
        # extract error msg
        error_msg = ""
        if compile_res == CompileResults.CodeError:
            error_msg = self.extract_error_msg(all_msg)
            save_code_to_file(error_msg, os.path.join(self.save_dir, f"build_error_{self.counter}.log"))
       
        # save raw error message
        save_code_to_file(all_msg, os.path.join(self.save_dir, f"build_{self.counter}.log"))

        return {"messages": compile_res, "build_msg": error_msg}


class CodeFixer:
    def __init__(self, runnable, compile_fix_prompt: str, fuzz_fix_prompt: str, max_fix: int, max_tool_call: int, 
                clear_msg_flag: bool, save_dir: str, cache_dir: str, code_callback=None , logger=None):

        self.runnable = runnable
        self.save_dir = save_dir
        self.cache_dir = cache_dir

        self.code_callback = code_callback
        self.logger = logger
        self.max_tool_call = max_tool_call
        self.max_fix = max_fix
        self.clear_msg_flag = clear_msg_flag
        self.fix_counter = 0
        self.tool_call_counter = 0
        self.compile_fix_prompt = compile_fix_prompt
        self.fuzz_fix_prompt = fuzz_fix_prompt

# def get_related_infos(cut_code, err_line, aainfo, api_funcs, examples):
	# related_infos = [ '' ]
	# if aainfo:
	# 	related_api = find_relevant_apis(cut_code, err_line, api_funcs)
	# 	if related_api != None:
	# 		related_api_info = get_api_info(api_funcs[related_api])
	# 		for related_api_usage in get_example_usages(examples, related_api):
	# 			related_info = '\nThe definition of `%s`:\n%s\n\n%s\n\n' % (related_api, related_api_info, related_api_usage)
	# 			related_infos.append(related_info)

    # if you need 
    def build_compile_prompt(self, harness_code, error_msg):
        '''
        Build the prompt for the code fixer. If you need to customize the prompt, you can override this function.
        '''
        return self.compile_fix_prompt.format(harness_code=harness_code, error_msg=error_msg)

    def build_fuzz_prompt(self, harness_code, error_msg):
        '''
        Build the prompt for the code fixer. If you need to customize the prompt, you can override this function.
        '''
        return self.fuzz_fix_prompt.format(harness_code=harness_code, error_msg=error_msg)

    def build_prompt(self, last_message: str, state: dict):

        if last_message.content == CompileResults.CodeError:
            return self.build_compile_prompt(state["harness_code"], state["build_msg"])
        else:
            return self.build_fuzz_prompt(state["harness_code"], state["fuzz_msg"])
    
    def respond(self, state: dict):
        last_message = state["messages"][-1]
        if isinstance(last_message, ToolMessage):
            return self._respond_helper(state)
        
        # not tool message
        if self.fix_counter == 0 or self.clear_msg_flag:
            # clear previous messages, need to build the prompt
            state["messages"].clear()
            fix_prompt = self.build_prompt(last_message, state)
        else:
            # keep the previous messages, just add the error message
            fix_prompt = state["build_msg"]  if last_message.content == CompileResults.CodeError else state["fuzz_msg"]

        state["messages"].append(("user", fix_prompt))
        return self._respond_helper(state)
    
    def _respond_helper(self, state: dict):


        response = self.runnable.invoke(state["messages"])

        # check if call the tool
        if len(response.tool_calls) != 0:
            self.tool_call_counter += 1
            if self.tool_call_counter > self.max_tool_call:
                self.logger.info(f"Max tool call times ({self.max_tool_call}) reached.")
                return {"messages": END}

            # tool call response
            return {"messages": response}

        self.fix_counter += 1
        if self.fix_counter > self.max_fix:
            self.logger.info(f"Max fix times ({self.max_fix}) reached")
            return {"messages": END}
        
        self.logger.info(f"Fix Counter: {self.fix_counter}")
        # extract the code
        source_code = self.code_callback(response.content)

        new_save_name = "draft_fix{}.txt".format(self.fix_counter)
        save_code_to_file(source_code, os.path.join(self.save_dir, new_save_name))
        # update the harness code
        return {"messages": ("assistant", response.content), "harness_code": source_code}


class FuzzerWraper(FuzzerRunner):
    def __init__(self, oss_fuzz_dir: str, new_project_name: str,
                 fuzzer_name: str, project_lang: str, run_timeout: int , 
                 save_dir: str, logger=None):

        super().__init__(oss_fuzz_dir, new_project_name, fuzzer_name, project_lang, run_timeout, save_dir)
        self.logger = logger

        
    def run_fuzzing(self, state: dict):
        self.logger.info(f"Run {self.counter+1}th Fuzzer for {self.new_project_name}:{self.fuzzer_name}")
       
        fuzz_res, error_type_line, stack_list = super().run_fuzzing()
        
        self.logger.info(f"Fuzz res:{fuzz_res}, {error_type_line} for {self.new_project_name}:{self.fuzzer_name}")
    
        # unable to fix the code
        if fuzz_res == FuzzResult.RunError:
            return {"messages": END}
        elif fuzz_res == FuzzResult.ConstantCoverageError:
            return {"messages": fuzz_res, "fuzz_msg": "The above code can be built successfully but its fuzzing seems not effective since the coverage never change. "}
        elif fuzz_res == FuzzResult.ReadLogError:
            return {"messages": fuzz_res, "fuzz_msg": "The above code can be built successfully but it generates a extreme large log which indicates the fuzz driver may include some bugs. "}
        elif fuzz_res == FuzzResult.Crash:
            # extract the first error message
            fuzz_error_msg = error_type_line[0] + "\n" + "\n".join(stack_list[0])
            return {"messages": fuzz_res, "fuzz_msg": fuzz_error_msg}
        else:
            return {"messages": fuzz_res}


class FuzzState(TypedDict):
    messages: Annotated[list, add_messages]
    harness_code: str
    build_msg: str
    build_res: CompileResults
    fuzz_msg: str

class AgentFuzzer():

    # Constants
    HarnessGenerator = "HarnessGenerator"
    Compiler = "Compiler"
    CodeFixer = "CodeFixer"
    FixerTools = "FixerTools"
    GenerationTools = "GenerationTools"
    Fuzzer = "Fuzzer"

    def __init__(self, model_name: str, ossfuzz_dir: str, project_name: str, function_signature: str,
                 run_time: int, max_fix: int, max_tool_call: int,  clear_msg_flag: bool,
                 save_dir: str, cache_dir: str):
        
        self.eailier_stop_flag = False

        self.model_name = model_name
        self.ossfuzz_dir = ossfuzz_dir
        self.project_name = project_name
        self.function_signature = function_signature
        self.run_time = run_time
        self.max_fix = max_fix
        self.max_tool_call = max_tool_call
        self.cache_dir = cache_dir
        self.clear_msg_flag = clear_msg_flag

        # random generate a string for new project name
        random_str = ''.join(random.choices("abcdefghijklmnopqrstuvwxyz", k=16))
        self.new_project_name = "{}_{}".format(project_name, random_str)
        self.save_dir = os.path.join(save_dir, self.new_project_name)
        self.logger = self.setup_logging()

        self.oss_tool = OSSFuzzUtils(self.ossfuzz_dir, self.project_name, self.new_project_name)
        self.project_lang = self.oss_tool.get_project_language()
        self.project_fuzzer_name,  self.project_harness_path = self.oss_tool.get_harness_and_fuzzer()

        self.docker_tool = DockerUtils(self.ossfuzz_dir, self.project_name, self.new_project_name, self.project_lang)
        init_flag = self.init_workspace()
        if not init_flag:
            self.eailier_stop_flag = True
            self.logger.error("Failed to initialize the workspace. Return")
            return 

    def setup_logging(self):

        # Create a logger
        logger = logging.getLogger(self.new_project_name)
        logger.setLevel(logging.INFO)

        # create the log file
        if not os.path.exists(self.save_dir):
            os.makedirs(self.save_dir)

        log_file = os.path.join(self.save_dir, 'agent.log')
        with open(log_file, 'w'):
            pass

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

        dst_path = os.path.join(self.ossfuzz_dir, "projects", self.new_project_name)
        if os.path.exists(dst_path):
            # clean the directory
            shutil.rmtree(dst_path)

        # create build log and fuzz log directory
        # os.makedirs(os.path.join(self.save_dir, "build_log"))
        # os.makedirs(os.path.join(self.save_dir, "fuzz_log"))
       
        # copy a backup of the project
        scr_path = os.path.join(self.ossfuzz_dir, "projects", self.project_name)
        shutil.copytree(scr_path, dst_path, dirs_exist_ok=True)

        # copy lsp related files to the project directory
        shutil.copy(os.path.join(PROJECT_PATH, "tools", "lsp_wrapper.py"), dst_path)
        shutil.copy(os.path.join(PROJECT_PATH, "tools", "clanglsp.py"), dst_path)
        shutil.copy(os.path.join(PROJECT_PATH, "tools", "language_parser.py"), dst_path)
        shutil.copy(os.path.join(PROJECT_PATH, "constants.py"), dst_path)



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
        
        # save the function name
        with open(os.path.join(self.save_dir, "function.txt"), 'w') as f:
            f.write(self.function_signature)

        # cache the compile_commands.json
        compile_res = self.cache_compile_commands()
        if not compile_res:
            return False
        
        return True


    def modify_dockerfile(self):
        '''Copy the harness file to overwrite all existing harness files'''
        # write copy in dockerfile and re-build the image
        project_path = os.path.join(self.ossfuzz_dir,  "projects", self.new_project_name)
        with open(os.path.join(project_path, 'Dockerfile'), 'a') as f:
            # Add additional statement in dockerfile to overwrite with generated fuzzer
            f.write(f'\nCOPY *.py  .\n')

            # wirte other commands
            f.write(f'\nRUN apt install -y clangd-18\n')
            # apt install bear
            f.write(f'\nRUN apt install -y bear\n')
            # install python library
            f.write("RUN pip install tree-sitter-c\n")
            f.write("RUN pip install tree-sitter-cpp\n")
            f.write("RUN pip install tree-sitter-java\n")
            f.write("RUN pip install tree-sitter\n")

    def cache_compile_commands(self) -> bool:
        
        cmd_json_name = "compile_commands.json"
        json_path = os.path.join(self.cache_dir, self.project_name, cmd_json_name)
        if os.path.exists(json_path):
            self.logger.info(f"Using Cached compile_commands.json")
            return True
       
        # make the cache directory
        dir_path = os.path.join(self.cache_dir, self.project_name)
        os.makedirs(dir_path, exist_ok=True)

        # run bear to generate compile_commands.json
        def bear_call_back(container):
            res = self.docker_tool.contrainer_exec_run(container, "bear compile")
            self.logger.info(f"bear res: {res.splitlines()[-2:]}")

            container_info = container.attrs 
            workdir = container_info['Config'].get('WorkingDir', '')
          
            try:
                sp_result = sp.run(f"docker cp {container.id}:{os.path.join(workdir, cmd_json_name)}  {dir_path}", shell=True, check=True, stdout=sp.PIPE, stderr=sp.PIPE)
            except Exception as e:
                self.logger.error(f"docker copy error: {e}")
                return False

            return True

        compile_cmd = self.docker_tool.run_docker_cmd(bear_call_back)

        if not compile_cmd:
            self.logger.error(f"No {cmd_json_name} , Exit")
            return False    
        return True

    def clean_workspace(self):
        '''Clean the workspace'''
        try:        
            # remove the docker image here
            self.docker_tool.remove_image()
            self.logger.info("remove the docker image for {}".format(self.new_project_name))
            # remove the project directory
            shutil.rmtree(os.path.join(self.ossfuzz_dir, "projects", self.new_project_name))

            # remove the corpus, needs root permission
            # corpus_dir = os.path.join(self.save_dir, "corpora")
            # if os.path.exists(corpus_dir):
                # shutil.rmtree(corpus_dir)
        except:
            pass


    def compile_router_mapping(self, state):
        last_message = state["messages"][-1]

        # print(messages)
        if last_message.content == CompileResults.Success:
            return self.Fuzzer
        elif last_message.content == CompileResults.CodeError:
            return self.CodeFixer
        else:
            return END

    def code_fixer_mapping(self, state):
        last_message = state["messages"][-1]

        if last_message.content == END:
            return END
        # call tools
        if len(last_message.tool_calls) != 0:
            return self.FixerTools
        else:
            return self.Compiler

    def generator_mapping(self, state):
        last_message = state["messages"][-1]

        if last_message.content == END:
            return END

        # call tools
        if len(last_message.tool_calls) != 0:
            return self.GenerationTools
        else:
            return self.Compiler
        
    def fuzzer_router_mapping(self, state):
        last_message = state["messages"][-1]

        # print(messages)
        if last_message.content == FuzzResult.NoError:
            return END
        elif last_message.content == END:
            return END
        else:
            return self.CodeFixer

    def build_graph(self):

        llm = ChatOpenAI(model=self.model_name)
        code_retriever = CodeRetriever(self.ossfuzz_dir, self.project_name, self.new_project_name, self.logger)
       
        # decide whether to use the tool according to the header_desc_mode    
        tools = []
        if self.header_desc_mode:
            tool = StructuredTool.from_function(
                    func=code_retriever.get_header_path,
                    name="get_header_path",
                    description=header_desc_mapping[ToolDescMode.Detailed],
                )
            tools.append(tool)
            code_fixer_prompt = CODE_FIX_PROMPT_TOOLS
        # header_desc_mode is none for disabling tool
        else:
            code_fixer_prompt = CODE_FIX_PROMPT

        if tools:
            # bind the tools, the tools may be empty
            llm_init_generator = llm.bind_tools(tools)
            llm_code_fixer = llm.bind_tools(tools)
        else:
            llm_code_fixer = llm
            llm_init_generator = llm

        # code formatter
        llm_code_extract = llm.with_structured_output(CodeAnswerStruct)
        code_formater = CodeFormatTool(llm_code_extract, EXTRACT_CODE_PROMPT)

        # read prompt according to the project language (extension of the harness file)
        if self.oss_tool.get_extension() == LanguageType.CPP:
           generator_prompt = load_pormpt_template(os.path.join(PROJECT_PATH, "prompts", "cpp_prompt.txt"))
        elif self.oss_tool.get_extension() == LanguageType.C:
            generator_prompt = load_pormpt_template(os.path.join(PROJECT_PATH, "prompts", "c_prompt.txt"))
        else:
            return 

        # create the module
        draft_responder = InitGenerator(llm_init_generator, self.save_dir, 
                                        code_callback=code_formater.extract_code, logger=self.logger)

        fuzz_fix_prompt = ""
        # code fixer needs old project name
        code_fixer = CodeFixer(llm_code_fixer, code_fixer_prompt, fuzz_fix_prompt, self.max_fix,
                                self.max_tool_call, self.clear_msg_flag, self.save_dir,
                               code_callback=code_formater.extract_code, logger=self.logger)

        fuzzer = FuzzerWraper(self.ossfuzz_dir, self.project_name, self.new_project_name,
                             self.project_fuzzer_name, self.run_time, self.save_dir, self.logger)

        compiler = CompilerWraper(self.oss_fuzz_dir, self.project_name, self.new_project_name, self.project_lang, self.save_dir, self.logger)

        # build the graph
        builder = StateGraph(FuzzState)
        memory = MemorySaver()

        # add nodes
        tool_node = ToolNode(tools)

        builder.add_node(self.HarnessGenerator, draft_responder.respond)
        builder.add_node(self.Compiler, compiler.compile_harness)
        builder.add_node(self.CodeFixer, code_fixer.respond)
        builder.add_node(self.FixerTools, tool_node)
        builder.add_node(self.GenerationTools, tool_node)
        builder.add_node(self.Fuzzer, fuzzer.run_fuzzing)

        # add edges
        builder.add_edge(START, self.HarnessGenerator)
        builder.add_edge(self.FixerTools, self.CodeFixer)
        builder.add_edge(self.GenerationTools, self.HarnessGenerator)

        # add conditional edges
        builder.add_conditional_edges(self.Compiler, self.compile_router_mapping,  [self.CodeFixer, self.Fuzzer, END])
        builder.add_conditional_edges(self.CodeFixer, self.code_fixer_mapping,  [self.Compiler, self.FixerTools, END])
        builder.add_conditional_edges(self.HarnessGenerator, self.generator_mapping,  [self.Compiler, self.GenerationTools, END])
        builder.add_conditional_edges(self.Fuzzer, self.fuzzer_router_mapping, [self.CodeFixer, END])

        # the path map is mandatory
        graph = builder.compile(memory)
        return graph

    def run_graph(self, graph):
        if self.eailier_stop_flag:
            return
        
        prompt = ""
        plot_graph(graph)
        config = {"configurable": {"thread_id": "1"}}
        events = graph.stream(
            {"messages": [("user", prompt)]},
            config,
            stream_mode="values",
        )

        for i, step in enumerate(events):
            # print(f"Step {i}")
            # step["messages"][-1].pretty_print()
            pass


def process_project(llm_name, ossfuzz_dir, project_name, function_name, save_dir, run_time, max_iterations, header_desc_mode):

    agent_fuzzer = AgentFuzzer(llm_name, ossfuzz_dir, project_name, function_name, save_dir, run_time, max_iterations, header_desc_mode)
    atexit.register(agent_fuzzer.clean_workspace)

    try:
        # Your main logic here
        agent_fuzzer.build_graph()
        agent_fuzzer.run_graph()

    except Exception as e:
        print(e)
        print("Program interrupted.")
    finally:
        agent_fuzzer.clean_workspace()

def run_parallel():
    # build graph
    ossfuzz_dir = "/home/yk/code/oss-fuzz/"
    # absolute path
    save_dir = os.path.join(PROJECT_PATH, "outputs")
    llm_name = "gpt-4o"
    run_time = 6
    max_iterations = 5
    header_desc_mode = "detailed"
    function_list = []
    # read benchmark names
    bench_dir = os.path.join(PROJECT_PATH, "benchmark-sets", "all")
    all_files = os.listdir(bench_dir)

    # sort 
    all_files.sort()

    # resume from project name
    # resume_project_name = "njs.yaml"
    # index = all_files.index(resume_project_name)
    index = 0

    for file in all_files[index:]:
        # read yaml file
        with open(os.path.join(bench_dir, file), 'r') as f:
            data = yaml.safe_load(f)
            project_name = data.get("project")
            lang_name = data.get("language")
            project_harness = data.get("target_path")

            if project_name != "tinyxml2":
                continue

            if lang_name not in ["c++", "c"]:
                continue
        
            for function in data.get("functions"):
                function_list.append((project_name, function["signature"]))

    print("total projects:", len(function_list))
    with Pool(processes=os.cpu_count()//2) as pool:

        for project_name, function_name in function_list:
            # pool.apply(process_project, args=(llm_name, ossfuzz_dir, project_name,function_name, save_dir, run_time, max_iterations))
            pool.apply_async(process_project, args=(llm_name, ossfuzz_dir, project_name, function_name, save_dir, run_time, max_iterations, header_desc_mode))

        pool.close()
        pool.join()



if __name__ == "__main__":

    # run_parallel()
    # exit()
    
    # build graph
    OSS_FUZZ_DIR = "/home/yk/code/oss-fuzz/"
    PROJECT_NAME = "tinyxml2"

    # absolute path
    SAVE_DIR = "/home/yk/code/LLM-reasoning-agents/outputs/"
    llm_name = "gpt-4o-mini"
    function_name = r"void tinyxml2::XMLElement::SetAttribute(const char *, const char *)"
    # function_name = r"OPJ_BOOL opj_jp2_get_tile(opj_jp2_t *, opj_stream_private_t *, opj_image_t *, opj_event_mgr_t *, OPJ_UINT32"
    # function_name = r"XMLError XMLDocument::LoadFile( const char* filename )"
    # function_name = "cJSON * cJSON_Parse(const char *)"
    # function_name = "void ares_gethostbyaddr(ares_channel_t *, const void *, int, int, ares_host_callback, void *)"
    # function_name = "void (anonymous namespace)::_RealWebSocket::operator()(struct CallbackAdapter *, const vector<unsigned char, std::__1::allocator<unsigned char> > &)"
    # function_name = "bool cpuinfo_linux_get_processor_core_id(uint32_t, DW_TAG_restrict_typeuint32_t *)"

    agent_fuzzer = AgentFuzzer(llm_name, OSS_FUZZ_DIR, PROJECT_NAME, function_name, SAVE_DIR, run_time=0.5, max_fix=5, header_desc_mode="detailed")
    atexit.register(agent_fuzzer.clean_workspace)
    
    # try:
    # Your main logic here
    # function_name = r"tinyxml2::XMLUnknown* tinyxml2::XMLElement::InsertNewUnknown(const char* text)"
    # function_name = r"CJSON_PUBLIC(cJSON *) cJSON_Duplicate(const cJSON *item, cJSON_bool recurse)"


    graph = agent_fuzzer.build_graph()
    agent_fuzzer.run_graph()
    