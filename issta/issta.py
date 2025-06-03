# This  code reimplementation of the algorithm from the ISSTA paper
from langchain_openai import ChatOpenAI
import os
from langgraph.graph import StateGraph, END, START  # type: ignore
import random
from constants import LanguageType, CompileResults, ToolDescMode, FuzzEntryFunctionMapping, LSPResults, Retriever, FuzzResult, LSPFunction
from langchain_core.tools import StructuredTool
from langgraph.prebuilt import ToolNode # type: ignore
from prompts.raw_prompts import  EXTRACT_CODE_PROMPT
from utils.misc import save_code_to_file, filter_examples, extract_name, add_lineno_to_code
from tools.code_retriever import CodeRetriever, header_desc_mapping
from agents.reflexion_agent import CodeFormatTool, InitGenerator, FuzzerWraper, CompilerWraper, CodeFixer, AgentFuzzer, FuzzState, CodeAnswerStruct
import io
import contextlib
import logging
import tiktoken
from issta.semantic_check import SemaCheck
from pathlib import Path
from typing import Any
from langchain_core.language_models import BaseChatModel


ISSTA_C_PROMPT = '''
// The following is a fuzz driver written in C language, complete the implementation. Output the continued code in reply only. 

// @ examples of API usage
{function_usage}

#include <stdint.h>
#include <stddef.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <errno.h>

{header_files}


{function_document}

extern {function_signature};
// the following function fuzzes {function_name}
extern int LLVMFuzzerTestOneInput(const uint8_t *Data, size_t Size);
int LLVMFuzzerTestOneInput(const uint8_t *Data, size_t Size)
'''

ISSTA_CPP_PROMPT = '''
// The following is a fuzz driver written in C++ language, complete the implementation. Output the continued code in reply only.


// @ examples of API usage 
{function_usage}


#include <stdint.h>
#include <stddef.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <errno.h>
{header_files}

{function_document}

extern {function_signature};

// the following function fuzzes {function_name}
extern "C" int LLVMFuzzerTestOneInput(const uint8_t *Data, size_t Size)
'''

compile_fix_prompt = '''
```
{harness_code}
```

The above {project_lang} code has compilation error.


The error description is: 
{error_msg}

[[SUPPLEMENTAL_INFO]]



Based on the above information, fix the code. Must provide the full fixed code. 
Your code must be compilable and runnable. 
Do not change the function signature of the harness function.
'''

fuzz_fix_prompt = '''
```
{harness_code}
```

The above {project_lang} code can be built successfully but has the following errors when runing fuzzer.

{error_msg}

Based on the above information, fix the code. Must provide the full fixed code. 
You code must be compilable and runnable. 
Do not change the function signature of the harness function. 

'''

tool_prompts = '''
You can call the following tools to get more information about the code:
- get_symbol_header: Get the header file of a symbol.
- get_symbol_definition: Get the definition of a symbol. (Details of the symbol, like the function body)
- get_symbol_declaration: Get the declaration of a symbol.
- view_code: View the code around the given file and targe line.
- get_struct_related_functions: Get the functions related to a struct. This can be used to get the functions that operate on a struct, like the initialization, destruction functions.
'''

REMOVED_FUNC = ['spdk_json_parse', 'GetINCHIfromINCHI', 'GetINCHIKeyFromINCHI', 'GetStructFromINCHI',
                'redisFormatCommand', 'stun_is_response', 'bpf_object__open_mem', 'lre_compile', 'JS_Eval', 
                'dwarf_init_path', 'dwarf_init_b', 'parse_privacy', 'luaL_loadbufferx', 'gf_isom_open_file',
                'zip_fread', 'dns_name_fromtext', 'dns_message_parse', 'isc_lex_getmastertoken', 
                'dns_rdata_fromwire', 'dns_name_fromwire', 'dns_master_loadbuffer', 'isc_lex_gettoken', 
                'dns_message_checksig', 'dns_rdata_fromtext']

class FixerPromptBuilder:
    def __init__(self, oss_fuzz_dir: Path,  project_name: str, new_project_name: str,
                 cache_dir: Path, usage_token_limit: int, logger: logging.Logger,
                 compile_fix_prompt: str, fuzz_fix_prompt: str, project_lang: LanguageType,
                    clear_msg_flag: bool):
        
        self.oss_fuzz_dir = oss_fuzz_dir
        self.new_project_name = new_project_name
        self.project_name = project_name
        self.cache_dir = cache_dir
        self.logger = logger
        self.usage_token_limit = usage_token_limit

        self.compile_fix_prompt = compile_fix_prompt
        self.fuzz_fix_prompt = fuzz_fix_prompt
        self.project_lang = project_lang
        self.clear_msg_flag = clear_msg_flag

    def build_compile_prompt(self, harness_code: str, error_msg: str)-> str:
        '''
        Build the prompt for the code fixer. If you need to customize the prompt, you can override this function.
        '''
        return self.compile_fix_prompt.format(harness_code=add_lineno_to_code(harness_code, 1), error_msg=error_msg, project_lang=self.project_lang)

    def build_fuzz_prompt(self, harness_code: str, error_msg: str)-> str:
        '''
        Build the prompt for the code fixer. If you need to customize the prompt, you can override this function.
        '''
        # extract 
        #1 0x5639df576b54 in ixmlDocument_createAttributeEx /src/pupnp/ixml/src/document.c:269:26
        reversed_stack = error_msg.split("\n")[::-1]
        index = None
        for i, line in enumerate(reversed_stack):
            if not line.strip().startswith("#"):
                continue
            # find the first api of the project in stack trace
            if "LLVMFuzzerTestOneInput" in line:
                index = i
                break
        
        if index and index+1 < len(reversed_stack):
            crash_line = reversed_stack[index+1]

            row_data = crash_line.strip().split(" ")

            # 5 for C
            if len(row_data) != 5:
                self.logger.info(f"Error message format is not correct: {crash_line}")
            else:
                _, _, _, func_name, _ = row_data
                
                retriever = CodeRetriever(self.oss_fuzz_dir, self.project_name, self.new_project_name, self.project_lang, self.cache_dir , self.logger)
                usages = retriever.get_symbol_references(func_name, Retriever.Parser)
                # filter the usage including the Function entry
                example = filter_examples(usages, self.project_lang, self.usage_token_limit)
               
                # comment the example
                comment_example = ""
                for line in example.splitlines():
                    # add comment
                    comment_example += "// " + line + "\n"
            
                error_msg += "\n\nThe crash line is: " + crash_line + "\n"
                # TODO 
                if comment_example != "":
                    error_msg += f"\n // the usage of {func_name} is as follows: \n" + comment_example

        return self.fuzz_fix_prompt.format(harness_code=add_lineno_to_code(harness_code, 1), error_msg=error_msg, project_lang=self.project_lang)

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

class SemaCheckNode:
    def __init__(self, oss_fuzz_dir: Path, project_name: str, new_project_name: str, 
                 function_signature: str, project_lang: LanguageType, logger: logging.Logger):
        self.oss_fuzz_dir = oss_fuzz_dir
        self.project_name = project_name
        self.new_project_name = new_project_name
        self.func_name = extract_name(function_signature)
        self.logger = logger
        self.checker = SemaCheck(oss_fuzz_dir, project_name, new_project_name, self.func_name, project_lang)

    def check(self, state: dict[str, Any]) -> dict[str, Any]:
        # self.logger.info("Semantic check passed")
        # return{"messages": ("user", END + "Semantic check passed")}
        # run semantic check
        flag = self.checker.check(state["harness_code"], state["fuzzer_path"], state["fuzzer_name"])
        if flag:
            self.logger.info("Semantic check passed")
            return{"messages": ("user", END + "Semantic check passed")}
        else:
            self.logger.info("Semantic check failed")
            msg = "The harness code is grammly correct, but it could not pass the semantic check. The reason is the harness code does not correctly fuzz the function." \
            " Maybe the harness code didn't correctly feed the fuzze data to correct position (like file or buffer)." 
            return{"messages": ("user", "Semantic check failed"), "fuzz_msg": msg}

class ISSTAFuzzer(AgentFuzzer):
    SemanticCheckNode = "SemanticCheckNode"
    def __init__(self, n_examples: int, example_mode: str, model_name: str, temperature:float, oss_fuzz_dir: Path, 
                 project_name: str, function_signature: str, usage_token_limit: int, 
                 model_token_limit: int, run_time: int, max_fix: int, max_tool_call: int,
                 clear_msg_flag:bool, save_dir: Path, cache_dir: Path, 
                 n_run: int = 1, tool_flag: bool=False):
        
        super().__init__(n_examples, example_mode, model_name, temperature, oss_fuzz_dir, project_name, function_signature, usage_token_limit, model_token_limit,
                         run_time, max_fix, max_tool_call, clear_msg_flag,  save_dir, cache_dir, n_run)
        self.n_examples = n_examples
        self.example_mode = example_mode
        self.model_token_limit = model_token_limit
        self.tool_flag = tool_flag

    def filer_examples(self, example_list: list[dict[str, str]]) -> list[dict[str, str]]:

        # filter the usage including the Function entry
        filter_code_usage: list[dict[str, str]] = []
        for code in example_list:
            if FuzzEntryFunctionMapping[self.project_lang] in code["source_code"]:
                continue
            # token limit
            if len(code["source_code"].split()) > self.usage_token_limit:
                continue
            filter_code_usage.append(code)

        return filter_code_usage

    def comment_example(self, example_list: list[dict[str, str]]) -> str:
        # leave some tokens for the prompt
        margin_token = self.usage_token_limit
        enc = tiktoken.encoding_for_model("gpt-4o")

        final_example_str = ""
        
        total_token = self.usage_token_limit
        n_used = 0
        for i, example in enumerate(example_list):
            function_usage = example["source_code"]
            function_usage = "\n//".join(function_usage.splitlines())
            function_usage = "\n// " + function_usage

            # token limit
            total_token += len(enc.encode(function_usage))
            if total_token > self.model_token_limit - margin_token:
                n_used = i-1
                break
            else:
                final_example_str += f"// Example {i+1}:\n" + function_usage + "\n"

        self.logger.info(f"Use {n_used+1} examples.")
        return final_example_str
    
    def select_example(self, example_list: list[dict[str, str]]) -> str:

        if self.n_examples == -1:
            self.n_examples = len(example_list)
        
        if self.example_mode == "random":
            
            random.shuffle(example_list)
            selected_list = example_list[:self.n_examples]
            return self.comment_example(selected_list)
        
        elif self.example_mode == "rank":

            rank_list:list[dict[str, str]] = []
            other_list:list[dict[str, str]] = []
            # first collect the rank examples
            for example in example_list:
                if "selection_score" not in example.keys():
                    other_list.append(example)
                elif int(example["selection_score"]) == 0:
                    other_list.append(example)
                else:
                    rank_list.append(example)
            
            # select the top n examples first from rank list, shuffle the rank list
            # shuffle the rank list
            random.shuffle(rank_list)
            random.shuffle(other_list)

            selected_list = rank_list[:self.n_examples]
            n_rest = self.n_examples - len(selected_list)

            if n_rest > 0:
                # if the rank list is empty, use the other examples
                selected_list += other_list[:n_rest]
            return self.comment_example(selected_list)
        
        return ""

    def build_init_prompt(self, prompt_template: str) -> str:

        # fill the template
        # {function_signature}

        # function_signature = self.function_signature
       
        # {function_name}
        # Remove the parameters by splitting at the first '('
        function_name = extract_name(self.function_signature)

        # {header_files}
        retriever = CodeRetriever(self.oss_fuzz_dir, self.project_name, self.new_project_name, LanguageType.C, self.cache_dir , self.logger)
        
        #TODO only include function header may not be enough
        header = retriever.get_symbol_header(function_name)

        if header == LSPResults.NoResult.value:
            self.logger.warning(f"No header found for {function_name}, Exit")
            self.eailier_stop_flag = True
            return ""

        header = f'#include "{header}"'

        # {function_usage}
        # get the function usage from the project and the public
        # project_code_usage = retriever.get_symbol_references(function_name, retriever=Retriever.LSP)
        project_code_usage = retriever.get_symbol_references(function_name, retriever=Retriever.Parser)
        filter_code_usage = self.filer_examples(project_code_usage)
        
        self.logger.info(f"Found {len(filter_code_usage)} usage in the project after removing harness.")
        if len(filter_code_usage) == 0:
            function_usage = ""
        else:
            function_usage = self.select_example(filter_code_usage)
        
        # TODO, no code from public do we need the namespace?
        # code_search = CodeSearch(function_name, self.new_project_name)

        # TODO, no document
        # {function_document}
        function_document = ""
        # {function_signature}
        # function_signature = self.function_signature
        retrieved_signature = retriever.get_symbol_info(function_name, lsp_function=LSPFunction.Declaration)
        if len(retrieved_signature) == 0:
            self.logger.warning(f"Can not retrieve signature for {function_name}, use the provided signature from xml")
        elif len(retrieved_signature) > 1:
            self.logger.warning(f"Multiple signature found for {function_name}, use the provided signature from xml")
        else:
            function_signature = retrieved_signature[0]["source_code"]
            if function_signature.replace(" ", "").replace("\n", "").replace("\t", "") != self.function_signature.replace(" ", "").replace("\n", "").replace("\t", "")+";":
                self.logger.error(f"Retrieved signature is different from the provided one: {function_signature} vs {self.function_signature}")

        prompt_template = prompt_template.format(header_files=header, function_usage=function_usage, function_document=function_document,
                                             function_signature=self.function_signature, function_name=function_name)
        prompt_template += "{\n"
        save_code_to_file(prompt_template, self.save_dir / "prompt.txt")

        return prompt_template


    def fuzzer_router_mapping(self, state:dict[str, Any]) -> str:
        last_message = state["messages"][-1]

        # print(messages)
        if last_message.content.startswith(FuzzResult.NoError.value):
            return self.SemanticCheckNode
        elif last_message.content.startswith(END):
            return END
        else:
            return self.FixBuilderNode
        
    def semantic_check_router_mapping(self,  state:dict[str, Any]) -> str:
        last_message = state["messages"][-1]

        if last_message.content.startswith(END):
            return END
        else:
            return self.FixBuilderNode
        
    def build_graph(self) -> StateGraph:

        llm = ChatOpenAI(model=self.model_name, temperature=self.temperature)

        code_retriever = CodeRetriever(self.oss_fuzz_dir, self.project_name, self.new_project_name, self.project_lang, self.cache_dir, self.logger)
        tools:list[StructuredTool] = []
        header_tool = StructuredTool.from_function(  # type: ignore
                func=code_retriever.get_symbol_header,
                name="get_symbol_header",
                description=header_desc_mapping[ToolDescMode.Detailed],
            )
        definition_tool = StructuredTool.from_function( # type: ignore
            func=code_retriever.get_symbol_definition,
            name="get_symbol_definition",
            description=code_retriever.get_symbol_definition.__doc__,
        )

        declaration_tool = StructuredTool.from_function( # type: ignore
            func=code_retriever.get_symbol_declaration,
            name="get_symbol_declaration",
            description=code_retriever.get_symbol_declaration.__doc__,
        )
        view_tool = StructuredTool.from_function( # type: ignore
            func=code_retriever.view_code,
            name="view_code",
            description=code_retriever.view_code.__doc__,
        )

        struct_tool = StructuredTool.from_function(  # type: ignore
            func=code_retriever.get_struct_related_functions,
            name="get_struct_related_functions",
            description=code_retriever.get_struct_related_functions.__doc__,
        )
        
        tools.append(header_tool)  
        if self.tool_flag:
            tools.append(definition_tool) 
            tools.append(declaration_tool)  
            tools.append(view_tool)  
            tools.append(struct_tool) 

        # code formatter
        llm_code_extract: BaseChatModel = llm.with_structured_output(CodeAnswerStruct) # type: ignore
        code_formater = CodeFormatTool(llm_code_extract, EXTRACT_CODE_PROMPT)
       
        tool_llm: BaseChatModel = llm.bind_tools(tools, parallel_tool_calls=False) # type: ignore


        draft_responder = InitGenerator(tool_llm, self.max_tool_call, continue_flag=True, save_dir=self.save_dir, 
                                        code_callback=code_formater.extract_code, logger=self.logger)

        global fuzz_fix_prompt, compile_fix_prompt
        if self.tool_flag:
            fuzz_fix_prompt += tool_prompts 
            compile_fix_prompt += tool_prompts 

        #  compile_fix_prompt: str, fuzz_fix_prompt: str, clear_msg_flag: bool)
        fix_builder = FixerPromptBuilder(self.oss_fuzz_dir, self.project_name, self.new_project_name, self.cache_dir, self.usage_token_limit, self.logger,
                                        compile_fix_prompt, fuzz_fix_prompt, self.project_lang, clear_msg_flag=self.clear_msg_flag)

        code_fixer = CodeFixer(tool_llm, self.max_fix, self.max_tool_call,  self.save_dir, self.cache_dir,
                                 code_callback=code_formater.extract_code, logger=self.logger)

        fuzzer = FuzzerWraper(self.oss_fuzz_dir, self.new_project_name, self.project_lang, 
                             self.run_time,  self.save_dir,  self.logger)
        
        compiler = CompilerWraper(self.oss_fuzz_dir, self.project_name, self.new_project_name, self.project_lang,
                                   self.harness_pairs, self.save_dir, self.logger)
        checker = SemaCheckNode(self.oss_fuzz_dir, self.project_name, self.new_project_name, self.function_signature, self.project_lang, self.logger)

        # build the graph
        builder = StateGraph(FuzzState)
        # add nodes
        tool_node = ToolNode(tools)

        builder.add_node(self.HarnessGeneratorNode, draft_responder.respond) # type: ignore
        builder.add_node(self.CompilerNode, compiler.compile)  # type: ignore
        builder.add_node(self.FixBuilderNode, fix_builder.respond)  # type: ignore
        builder.add_node(self.CodeFixerNode, code_fixer.respond)  # type: ignore
        builder.add_node(self.FixerToolNode, tool_node) # type: ignore
        builder.add_node(self.GenerationToolNode, tool_node) # type: ignore
        builder.add_node(self.FuzzerNode, fuzzer.run_fuzzing) # type: ignore
        builder.add_node(self.SemanticCheckNode, checker.check) # type: ignore

        # add edges
        builder.add_edge(START, self.HarnessGeneratorNode)
        builder.add_edge(self.FixerToolNode, self.CodeFixerNode)
        builder.add_edge(self.FixBuilderNode, self.CodeFixerNode)
        builder.add_edge(self.GenerationToolNode, self.HarnessGeneratorNode)

        # add conditional edges
        builder.add_conditional_edges(self.HarnessGeneratorNode, self.generator_mapping,  [self.CompilerNode, self.GenerationToolNode, END])
        builder.add_conditional_edges(self.CompilerNode, self.compile_router_mapping,  [self.FixBuilderNode, self.FuzzerNode, END])
        builder.add_conditional_edges(self.CodeFixerNode, self.code_fixer_mapping,  [self.CompilerNode, self.FixerToolNode, END])
        builder.add_conditional_edges(self.FuzzerNode, self.fuzzer_router_mapping, [self.FixBuilderNode,  self.SemanticCheckNode, END])
        builder.add_conditional_edges(self.SemanticCheckNode, self.semantic_check_router_mapping, [self.FixBuilderNode, END])

        # the path map is mandatory
        graph: StateGraph = builder.compile() # type: ignore
        return graph


    def run_graph(self, graph: StateGraph) -> None:
        if self.eailier_stop_flag:
            return
        
        # read prompt according to the project language (extension of the harness file)
        if self.oss_tool.get_extension(None) == LanguageType.CPP:
            generator_prompt_temlpate = ISSTA_CPP_PROMPT
        elif self.oss_tool.get_extension(None) == LanguageType.C:
            generator_prompt_temlpate = ISSTA_C_PROMPT
        else:
            return 
        
        # build the prompt for initial generator
        generator_prompt = self.build_init_prompt(generator_prompt_temlpate)
        if self.eailier_stop_flag:
            return
        
        # plot_graph(graph)
        config = {"configurable": {"thread_id": "1"}, "recursion_limit": 200} # type: ignore
        events = graph.stream( # type: ignore
            {"messages": [("user", generator_prompt)]},
            config,
            stream_mode="values",
        )

        with open(os.path.join(self.save_dir, "output.log"), "w") as f:
            for i, step in enumerate(events): # type: ignore
                f.write(f"Step {i}\n")  # Save step number if needed
                output = io.StringIO()  # Create an in-memory file-like object
                with contextlib.redirect_stdout(output):  # Capture print output
                    step["messages"][-1].pretty_print() # type: ignore
                f.write(output.getvalue() + "\n")  # Write captured output to file

                f.flush()
                