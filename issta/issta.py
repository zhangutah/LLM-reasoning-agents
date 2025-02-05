# This  code reimplementation of the algorithm from the ISSTA paper
from typing import Annotated
import subprocess as sp
from langchain_core.messages import ToolMessage
from langchain_openai import ChatOpenAI
from typing_extensions import TypedDict
from langgraph.graph.message import add_messages, MessagesState
import os
from langgraph.checkpoint.memory import MemorySaver
from langgraph.graph import END, StateGraph, START
import random
from constants import LanguageType, CompileResults, PROJECT_PATH, ToolDescMode, FuzzEntryFunctionMapping, LSPResults
from pydantic import BaseModel, Field
import logging
import shutil
from langchain_core.tools import tool, BaseTool, StructuredTool
from langgraph.prebuilt import ToolNode
from langchain_anthropic import ChatAnthropic
from multiprocessing import Pool, Lock, Manager
import yaml
import atexit
import asyncio
from prompts.raw_prompts import CODE_FIX_PROMPT, EXTRACT_CODE_PROMPT, CODE_FIX_PROMPT_TOOLS
from utils.misc import plot_graph, load_pormpt_template, save_code_to_file
from tools.code_retriever import CodeRetriever, header_desc_mapping
from tools.code_search import CodeSearch
from agents.reflexion_agent import CodeFormatTool, InitGenerator, FuzzerWraper, CompilerWraper, CodeFixer, AgentFuzzer, FuzzState, CodeAnswerStruct
import io
import contextlib
import atexit
import signal
import sys


ISSTA_C_PROMPT = '''
// The following is a fuzz driver written in C language, complete the implementation. Output the continued code in reply only.

{header_files}
// @ examples of API usage
{function_usage}

{function_document}

extern {function_signature};

// the following function fuzzes {function_name}
extern int LLVMFuzzerTestOneInput(const uint8_t *Data, size_t Size)
'''

ISSTA_CPP_PROMPT = '''
// The following is a fuzz driver written in C++ language, complete the implementation. Output the continued code in reply only.

{header_files}

// @ examples of API usage 
{function_usage}

{function_document}

extern {function_signature};

// the following function fuzzes {function_name}
extern "C" int LLVMFuzzerTestOneInput(const uint8_t *Data, size_t Size) 
'''

compile_fix_prompt = '''
```
{harness_code}
```

The above C code has compilation error.


The error description is: 
{error_msg}

[[SUPPLEMENTAL_INFO]]



Based on the above information, fix the code.
'''

fuzz_fix_prompt = '''
```
{harness_code}
```

The above C code can be built successfully but has the following errors when runing fuzzer.

{error_msg}

Based on the above information, fix the code.

'''

class ISSTAFuzzer(AgentFuzzer):
    def __init__(self, model_name: str, ossfuzz_dir: str, project_name: str, function_signature: str,
                 run_time: int, max_fix: int, max_tool_call: int, clear_msg_flag:bool, save_dir: str, cache_dir: str):
        super().__init__(model_name, ossfuzz_dir, project_name, function_signature, run_time, 
                        max_fix, max_tool_call, clear_msg_flag,  save_dir, cache_dir)


    def build_init_prompt(self, prompt_template):

        # fill the template
        # {function_signature}
        function_signature = self.function_signature

        # {function_name}
        # Remove the parameters by splitting at the first '('
        function_name = function_signature.split('(')[0]
        # Split the function signature into tokens to isolate the function name
        tokens = function_name.strip().split()
        assert len(tokens) > 0

        # The function name is the last token, this may include namespaces ::
        function_name = tokens[-1]
        # remove * from the function name
        if "*" in function_name:
            function_name = function_name.replace("*", "")

        # {header_files}
        retriever = CodeRetriever(self.ossfuzz_dir, self.project_name, self.new_project_name, self.project_lang, self.cache_dir , self.logger)
        
        #TODO only include function header may not be enough
        header = retriever.get_symbol_header(function_name)

        if header == LSPResults.NoResult:
            self.logger.warning(f"No header found for {function_name}, Exit")
            self.eailier_stop_flag = True
            return ""

        header = f'#include "{header}"'

        # {function_usage}
        # get the function usage from the project and the public
        project_code_usage = retriever.get_symbol_references(function_name)
        
        # filter the usage including the Function entry
        filter_code_usage = []
        for code in project_code_usage:
            if FuzzEntryFunctionMapping[self.project_lang] not in code["source_code"]:
                filter_code_usage.append(code)
        
        self.logger.info(f"Found {len(filter_code_usage)} usage in the project after removing harness.")
        if len(filter_code_usage) == 0:
            function_usage = ""
        else:
            # randomly select one usage
            random_index = random.randint(0, len(filter_code_usage) - 1)
            function_usage = filter_code_usage[random_index]["source_code"]

            #  add comment for function usage
            comment_function_usage = []
            for line in function_usage.split("\n"):
                comment_function_usage.append(f"// {line}")
            function_usage = "\n".join(comment_function_usage)
            self.logger.info(f"Using {random_index}th usage in the project.")
            
        # TODO, no code from public do we need the namespace?
        # code_search = CodeSearch(function_name, self.new_project_name)

        # TODO, no document
        # {function_document}
        function_document = ""

        prompt_template = prompt_template.format(header_files=header, function_usage=function_usage, function_document=function_document,
                                             function_signature=function_signature, function_name=function_name)
        prompt_template += "{\n"
        save_code_to_file(prompt_template, os.path.join(self.save_dir, "prompt.txt"))

        return prompt_template

    def build_graph(self):

        llm = ChatOpenAI(model=self.model_name)

        code_retriever = CodeRetriever(self.ossfuzz_dir, self.project_name, self.new_project_name, self.project_lang, self.cache_dir, self.logger)
        tools = []
        header_tool = StructuredTool.from_function(
                func=code_retriever.get_symbol_header,
                name="get_symbol_header",
                description=header_desc_mapping[ToolDescMode.Detailed],
            )
        tools.append(header_tool)

        # code formatter
        llm_code_extract = llm.with_structured_output(CodeAnswerStruct)
        code_formater = CodeFormatTool(llm_code_extract, EXTRACT_CODE_PROMPT)


        draft_responder = InitGenerator(llm, self.max_tool_call, continue_flag=True, save_dir=self.save_dir, 
                                        code_callback=code_formater.extract_code, logger=self.logger)

        fixer_llm = llm.bind_tools(tools)

        #  runnable, compile_fix_prompt: str, fuzz_fix_prompt: str, max_tool_call: int, max_fix: int, 
                # clear_msg_flag: bool, save_dir: str, cache_dir: str, code_callback=None , logger=None)
        
        code_fixer = CodeFixer(fixer_llm, compile_fix_prompt, fuzz_fix_prompt, self.max_fix, self.max_tool_call, self.clear_msg_flag,
                                self.save_dir, self.cache_dir, code_callback=code_formater.extract_code, logger=self.logger)

        fuzzer = FuzzerWraper(self.ossfuzz_dir, self.new_project_name, self.project_fuzzer_name, 
                            self.project_lang,  self.run_time,  self.save_dir,  self.logger)
        
        compiler = CompilerWraper(self.ossfuzz_dir, self.project_name, self.new_project_name, self.project_lang, self.save_dir, self.logger)


        # build the graph
        builder = StateGraph(FuzzState)
        # add nodes
        tool_node = ToolNode(tools)

        builder.add_node(self.HarnessGenerator, draft_responder.respond)
        builder.add_node(self.Compiler, compiler.compile_harness)
        builder.add_node(self.CodeFixer, code_fixer.respond)
        builder.add_node(self.FixerTools, tool_node)
        builder.add_node(self.Fuzzer, fuzzer.run_fuzzing)

        # add edges
        builder.add_edge(START, self.HarnessGenerator)
        builder.add_edge(self.HarnessGenerator, self.Compiler)
        builder.add_edge(self.FixerTools, self.CodeFixer)

        # add conditional edges
        builder.add_conditional_edges(self.Compiler, self.compile_router_mapping,  [self.CodeFixer, self.Fuzzer, END])
        builder.add_conditional_edges(self.CodeFixer, self.code_fixer_mapping,  [self.Compiler, self.FixerTools, END])
        builder.add_conditional_edges(self.Fuzzer, self.fuzzer_router_mapping, [self.CodeFixer, END])

        # the path map is mandatory
        graph = builder.compile()
        return graph


    def run_graph(self, graph):
        if self.eailier_stop_flag:
                    return
        
        # read prompt according to the project language (extension of the harness file)
        if self.oss_tool.get_extension() == LanguageType.CPP:
            generator_prompt_temlpate = ISSTA_CPP_PROMPT
        elif self.oss_tool.get_extension() == LanguageType.C:
            generator_prompt_temlpate = ISSTA_C_PROMPT
        else:
            return 
        
        # build the prompt for initial generator
        generator_prompt = self.build_init_prompt(generator_prompt_temlpate)

        # plot_graph(graph)
        config = {"configurable": {"thread_id": "1"}}
        events = graph.stream(
            {"messages": [("user", generator_prompt)]},
            config,
            stream_mode="values",
        )

        with open(os.path.join(self.save_dir, "output.log"), "w") as f:
            for i, step in enumerate(events):
                f.write(f"Step {i}\n")  # Save step number if needed
                output = io.StringIO()  # Create an in-memory file-like object
                with contextlib.redirect_stdout(output):  # Capture print output
                    step["messages"][-1].pretty_print()
                f.write(output.getvalue() + "\n")  # Write captured output to file

                f.flush()


def process_project(llm_name, ossfuzz_dir, project_name, function_signature, run_time, max_fix, max_tool_call, save_dir, cache_dir):

    agent_fuzzer = ISSTAFuzzer(llm_name, ossfuzz_dir, project_name, function_signature, run_time=run_time, max_fix=max_fix,
                                    max_tool_call=max_tool_call, clear_msg_flag=True, save_dir=save_dir, cache_dir=cache_dir)
    
   
    atexit.register(agent_fuzzer.clean_workspace)

    try:
        # Your main logic here
        graph = agent_fuzzer.build_graph()
        agent_fuzzer.run_graph(graph)

    except Exception as e:
        agent_fuzzer.logger.error(f"Exit. An exception occurred: {e}")
        print(f"Program interrupted. from {e} ")
    finally:
        agent_fuzzer.clean_workspace()

def run_parallel():
    # build graph
    ossfuzz_dir = "/home/yk/code/oss-fuzz/"
    # absolute path
    save_dir = os.path.join(PROJECT_PATH, "outputs")
    cache_dir = "/home/yk/code/LLM-reasoning-agents/cache/"
    llm_name = "gpt-4-0613"
    run_time=0.5
    max_fix=5
    max_tool_call=15
    function_list = []
    # read benchmark names
    bench_dir = os.path.join(PROJECT_PATH, "benchmark-sets", "ntu")
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
# 
            if project_name != "igraph":
                continue

            if lang_name not in ["c++", "c"]:
                continue
        
            for function in data.get("functions"):
                function_list.append((project_name, function["signature"].replace("\n", "")))

    print("total projects:", len(function_list))
    # os.cpu_count()//2
    with Pool(processes=os.cpu_count()//2) as pool:

        for project_name, function_signature in function_list:
            # llm_name, ossfuzz_dir, project_name, function_signature, run_time, max_fix, max_tool_call, save_dir, cache_dir
            # pool.apply(process_project, args=(llm_name, ossfuzz_dir, project_name, function_signature, run_time, max_fix, max_tool_call,  save_dir, cache_dir))
            pool.apply_async(process_project, args=(llm_name, ossfuzz_dir, project_name, function_signature, run_time, max_fix, max_tool_call,  save_dir, cache_dir))

        pool.close()
        pool.join()


if __name__ == "__main__":


    run_parallel()
    exit()
    # 
    # build graph
    OSS_FUZZ_DIR = "/home/yk/code/oss-fuzz/"
    PROJECT_NAME = "igraph"

    # absolute path
    SAVE_DIR = "/home/yk/code/LLM-reasoning-agents/outputs/issta"
    CACHE_DIR = "/home/yk/code/LLM-reasoning-agents/cache/"
    # llm_name = "gpt-4o-mini"
    llm_name = "gpt-4-0613"
    # function_signature = r"isc_result_t dns_name_fromwire(const dns_name_t *, const isc_buffer_t *, const dns_decompress_t, isc_buffer_t *)"
    function_signature = r"igraph_error_t igraph_automorphism_group(const igraph_t *, const igraph_vector_int_t *, igraph_vector_int_list_t *)"
#  model_name: str, ossfuzz_dir: str, project_name: str, function_signature: str,
                #  run_time: int, max_fix: int, max_tool_call: int, clear_msg_flag:bool, save_dir: str, cache_dir: str)
    # model_name: str, ossfuzz_dir: str, project_name: str, function_signature: str,
                #  run_time: int, max_fix: int, max_tool_call: int, clear_msg_flag:bool, save_dir: str, cache_dir: str
    
    agent_fuzzer = ISSTAFuzzer(llm_name, OSS_FUZZ_DIR, PROJECT_NAME, function_signature, run_time=0.5, max_fix=5,
                                max_tool_call=10, clear_msg_flag=True, save_dir=SAVE_DIR, cache_dir=CACHE_DIR)
   
   
    atexit.register(agent_fuzzer.clean_workspace)

    def signal_handler(sig, frame):
        print(f"Received signal {sig}, cleaning up...")
        agent_fuzzer.clean_workspace()
        sys.exit(0)

    # Register the signal handler for SIGINT (Ctrl+C) and SIGTERM
    signal.signal(signal.SIGINT, signal_handler)
    signal.signal(signal.SIGTERM, signal_handler)

    try:
        graph = agent_fuzzer.build_graph()
        agent_fuzzer.run_graph(graph)
    except Exception as e:
        print(f"An exception occurred: {e}")
    finally:
        agent_fuzzer.clean_workspace()
