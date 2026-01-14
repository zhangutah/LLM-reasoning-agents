import os
import io
import contextlib
import logging
import tiktoken
import json
from pathlib import Path
from langgraph.graph import StateGraph, END, START  # type: ignore
from constants import LanguageType, FuzzEntryFunctionMapping, Retriever, ValResult, CompileResults, PROJECT_PATH
from langchain_core.tools import StructuredTool
from langgraph.prebuilt import ToolNode # type: ignore
from utils.misc import save_code_to_file, extract_name, load_prompt_template, is_empty_json_file
from agent.modules.fuzzenv import FuzzENV
from agent.header.universal import HeaderCompilerWraper
from agent.fixing.raw import FixerPromptBuilder
from agent.fixing.issta import ISSTAFixerPromptBuilder
from agent.fixing.oss_fuzz import OSSFUZZFixerPromptBuilder
from agent.modules.code_format import CodeFormatTool, CodeAnswerStruct
from agent.modules.compilation import CompilerWraper
from agent.modules.validation import Validation
from agent.modules.generator import HarnessGenerator
from agent.modules.fixer import CodeFixer
from agent.modules.semantic_check import SemaCheck
from typing import Any, Optional
from langchain_core.language_models import BaseChatModel
from bench_cfg import BenchConfig
from agent_tools.code_search import search_public_usage
from agent_tools.example_selection import cache_example_selection
from constants import CodeSearchAPIName
from langgraph.checkpoint.memory import MemorySaver
from ossfuzz_gen import benchmark as benchmarklib
from ossfuzz_gen.context_introspector import ContextRetriever
from typing import Annotated
from langchain_openai import ChatOpenAI
from langchain_core.language_models import BaseChatModel
from typing_extensions import TypedDict
from langgraph.graph.message import add_messages # type: ignore

class FuzzState(TypedDict):
    messages: Annotated[list[str], add_messages]
    harness_code: str
    build_msg: str
    fuzz_msg: str
    fix_counter: int
    fuzzer_name: str
    fuzzer_path: Path
    function_signature: str


class SemaCheckNode:
    def __init__(self, oss_fuzz_dir: Path, benchmark_dir: Path, project_name: str, new_project_name: str, 
                 function_signature: str, project_lang: LanguageType, mode: str, logger: logging.Logger):
        self.oss_fuzz_dir = oss_fuzz_dir
        self.project_name = project_name
        self.new_project_name = new_project_name
        self.mode = mode
        self.func_name = extract_name(function_signature, keep_namespace=True, language=project_lang)
        self.logger = logger
        self.checker = SemaCheck(oss_fuzz_dir, benchmark_dir, project_name, new_project_name, self.func_name, project_lang)

    def check(self, state: dict[str, Any]) -> dict[str, Any]:
        
        if self.mode == "no":
            self.logger.info("No semantic check")
            return {"messages": ("user", END)}

        # run semantic check
        flag = self.checker.check(state["harness_code"], state["fuzzer_path"], state["fuzzer_name"])
        if flag:
            self.logger.info("Semantic check passed")
            return{"messages": ("user", END)}
        else:
            self.logger.info("Semantic check failed")
            if self.mode == "both":
                msg = "The harness code is grammly correct, but it could not pass the semantic check. The reason is the harness code does not correctly fuzz the function." \
                " Maybe the harness code didn't correctly feed the fuzze data to correct position (like file or buffer)." 
                return{"messages": ("user", "Semantic check failed"), "fuzz_msg": msg}
            # if not for generation, return;
            elif self.mode == "eval":
                return{"messages": ("user", END)}
            else:
                raise ValueError(f"Unknown semantic check mode: {self.mode}")

class ISSTAFuzzer(FuzzENV):
    # Constants
    HarnessGeneratorNode = "HarnessGenerator"
    CompilerNode = "Compiler"
    CodeFixerNode = "CodeFixer"
    FixerToolNode = "FixerTools"
    GenerationToolNode = "GenerationTools"
    FuzzerNode = "Fuzzer"
    FixBuilderNode = "FixBuilder"
    SemanticCheckNode = "SemanticCheckNode"
   
    def __init__(self, benchcfg: BenchConfig, function_signature: str, project_name: str, n_run: int):
        super().__init__(benchcfg, function_signature, project_name, n_run)

        self.oss_fuzz_benchmark = self.get_oss_fuzz_benchmark()
        
        if self.benchcfg.fixing_mode == "agent":
            self.tool_prompt = load_prompt_template(f"{PROJECT_PATH}/agent/prompts/tool_prompt.txt")
        elif self.benchcfg.header_mode == "agent":
            self.tool_prompt = load_prompt_template(f"{PROJECT_PATH}/agent/prompts/header_prompt.txt")
        else:
            self.tool_prompt = ""

        if self.project_lang == LanguageType.JAVA:
            self.tool_prompt = ""
        
        if self.code_retriever is None:
            raise ValueError("Code retriever is not initialized in FuzzENV.")
        
    def get_oss_fuzz_benchmark(self) -> Optional[benchmarklib.Benchmark]:
        benchmark_file =  self.benchcfg.benchmark_dir / "{}.yaml".format(self.project_name)
        benchmark_list = benchmarklib.Benchmark.from_yaml(str(benchmark_file))

        # find the benchmark with the function name
        oss_fuzz_benchmark = None
        for benchmark in benchmark_list:
            if benchmark.function_name == extract_name(self.function_signature, keep_namespace=False, language=self.project_lang):
                oss_fuzz_benchmark = benchmark
                break
        if self.benchcfg.header_mode == "oss_fuzz" and oss_fuzz_benchmark is None:
            raise ValueError(f"No OSS-Fuzz benchmark found for {self.function_signature} in {benchmark_file}")
        if self.benchcfg.fixing_mode == "oss_fuzz" and oss_fuzz_benchmark is None:
            raise ValueError(f"No OSS-Fuzz benchmark found for {self.function_signature} in {benchmark_file}")
        
        return oss_fuzz_benchmark

    def filter_examples(self, example_list: list[dict[str, str]]) -> list[dict[str, str]]:

        # filter the usage including the Function entry
        filter_code_usage: list[dict[str, str]] = []
        for code in example_list:
            if FuzzEntryFunctionMapping[self.project_lang] in code["source_code"]:
                continue
            # token limit
            if len(code["source_code"].split()) > self.benchcfg.usage_token_limit:
                continue
            filter_code_usage.append(code)

        return filter_code_usage

    def get_header(self, function_name: str) -> str:
        self.logger.info(f"Using {self.benchcfg.header_mode} for header files")
        
        if self.benchcfg.header_mode == "static":
            assert self.code_retriever is not None, "Code retriever is not initialized."
            headers = self.code_retriever.get_symbol_header(function_name)
            headers = headers.splitlines()

            if self.oss_fuzz_benchmark and self.oss_fuzz_benchmark.params:
                for param in self.oss_fuzz_benchmark.params:
                    type_headers = self.code_retriever.get_symbol_header(param["type"])
                    # add the header files from the benchmark
                    headers += type_headers.splitlines()
            
            if self.oss_fuzz_benchmark and self.oss_fuzz_benchmark.return_type:
                return_headers = self.code_retriever.get_symbol_header(self.oss_fuzz_benchmark.return_type)
                # add the header files from the benchmark
                headers += return_headers.splitlines()  

        elif self.benchcfg.header_mode == "all":
            # added during compile time, Too many headers may cause the prompt to be too long
            headers = []
        elif self.benchcfg.header_mode == "agent":
            headers = []
        elif self.benchcfg.header_mode == "oss_fuzz":

            # read from cache.
            cache_file = self.benchcfg.cache_root / f"{function_name}_oss_headers.json"
            if cache_file.exists():
                with open(cache_file, "r") as f:
                    headers = json.load(f)
                    self.logger.info(f"Loaded headers from cache: {headers}")
                    return headers

            retriever = ContextRetriever(self.oss_fuzz_benchmark)  # type: ignore
            # context_info = retriever.get_context_info()
            files = retriever._get_files_to_include() # type: ignore
            header = retriever.get_prefixed_header_file()
            headers = [header] + files
            # filter the headers to only include the header files
            headers = [h for h in headers if h and  h.endswith(('.h', '.hxx', '.hpp'))] # type: ignore
            if len(headers) == 0:
                self.logger.warning(f"No header files found for {function_name} in OSS-Fuzz benchmark, use empty string")
        elif self.benchcfg.header_mode == "no":
            headers = []
            self.logger.info("No header files used")
        else:
            raise ValueError(f"Unknown header mode: {self.benchcfg.header_mode}")
    
        header_string = ""
        for h in headers:
            if "no result" in h:
                continue
            header_string += f'#include "{h}"\n'
        
        return header_string

    def comment_example(self, example_list: list[dict[str, str]]) -> str:
        # leave some tokens for the prompt
        margin_token = self.benchcfg.usage_token_limit
        enc = tiktoken.encoding_for_model("gpt-4o")

        final_example_str = ""

        total_token = self.benchcfg.usage_token_limit
        n_used = 0
        for i, example in enumerate(example_list):
            function_usage = example["source_code"]
            function_usage = "\n//".join(function_usage.splitlines())
            function_usage = "\n// " + function_usage

            # token limit
            total_token += len(enc.encode(function_usage))
            if total_token > self.benchcfg.model_token_limit - margin_token:
                n_used = i-1
                break
            else:
                final_example_str += f"// Example {i+1}:\n" + function_usage + "\n"

        self.logger.info(f"Use {n_used+1} examples.")
        return final_example_str
   
    def select_example(self, example_list: list[dict[str, str]]) -> str:

        if self.benchcfg.n_examples == 0:
            self.logger.info("No examples selected, return empty string")
            return ""
        
        if self.benchcfg.n_examples == -1:
            self.benchcfg.n_examples = len(example_list)
        
        if self.benchcfg.example_mode == "random":
            
            # do not repeat the examples for different runs
            selected_list = example_list[(self.n_run-1)*self.benchcfg.n_examples:self.n_run*self.benchcfg.n_examples]
            return self.comment_example(selected_list)
        
        elif self.benchcfg.example_mode == "rank":

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
            # random.shuffle(rank_list)
            # random.shuffle(other_list)

            # do not repeat the examples for different runs
            selected_list = rank_list[(self.n_run-1)*self.benchcfg.n_examples:self.n_run*self.benchcfg.n_examples]
            n_rest = self.benchcfg.n_examples - len(selected_list)

            if n_rest > 0:
                # if the rank list is empty, use the other examples
                selected_list += other_list[(self.n_run-1)*n_rest:self.n_run*n_rest]
            return self.comment_example(selected_list)
        
        return ""

    def get_project_usage(self, function_name: str) -> list[dict[str, str]]:
        # save to cache json file
        example_json_file = self.benchcfg.cache_root / self.project_name / f"{function_name}_references_mixed.json"
        if example_json_file.exists() and not is_empty_json_file(example_json_file):
            with open(example_json_file, "r") as f:
                code_usages = json.load(f)
            self.logger.info(f"Loaded code usages from cache: {example_json_file}")
            return code_usages
        
        assert self.code_retriever is not None, "Code retriever is not initialized."
        lsp_code_usages = self.code_retriever.get_all_symbol_references(function_name, retriever=Retriever.LSP)
        parser_code_usages = self.code_retriever.get_all_symbol_references(function_name, retriever=Retriever.Parser)
        code_usages = lsp_code_usages + parser_code_usages
        # deduplicate the code usages based on source code
        unique_sources: set[str] = set()
        unique_code_usages: list[dict[str, str]] = []
        for usage in code_usages:
            if usage["source_code"] and usage["source_code"] not in unique_sources:
                unique_sources.add(usage["source_code"])
                unique_code_usages.append(usage)

        code_usages = unique_code_usages
      
        with open(example_json_file, "w") as f:
            json.dump(unique_code_usages, f, indent=4)

        return code_usages
            
    def get_function_usage(self, function_name: str) -> str:
        self.logger.info(f"Using {self.benchcfg.example_source} for example source")

        if self.benchcfg.example_source == CodeSearchAPIName.Sourcegraph:
            code_usages = search_public_usage(CodeSearchAPIName.Sourcegraph, function_name, self.project_name, self.project_lang, self.benchcfg)
            example_json_file = self.benchcfg.cache_root / self.project_name / f"{function_name}_references_{CodeSearchAPIName.Sourcegraph.value}.json"
        else:
            code_usages = self.get_project_usage(function_name)
            example_json_file = self.benchcfg.cache_root / self.project_name / f"{function_name}_references_mixed.json"

        if self.benchcfg.example_mode == "rank":
            self.logger.info("Using rank mode for example selection")
            code_usages = cache_example_selection(example_json_file, function_name, self.project_name, self.benchcfg.model_name)
        
        filter_code_usage = self.filter_examples(code_usages) # type: ignore

        self.logger.info(f"Found {len(filter_code_usage)} usage in the project after removing harness.")
        
        function_usage = ""
        if len(filter_code_usage) > 0:
            function_usage = self.select_example(filter_code_usage)
        return function_usage
        
    def build_init_prompt(self, prompt_template: str) -> str:

        # from constants import LSPFunction
        # self.code_retriever.get_symbol_info("All", LSPFunction.AllSymbols, Retriever.Parser)
        # exit()
        # {function_name}
        # Remove the parameters by splitting at the first '('
        function_name = extract_name(self.function_signature, keep_namespace=True, 
                                     exception_flag=False, language=self.project_lang)
        
        # {header_files}
        header_string = self.get_header(function_name)

        # get the function usage from the project and the public
        function_usage = self.get_function_usage(function_name)

        # TODO, no document
        # {function_document}
        contexts = ""
        if self.benchcfg.definition_flag:
            assert self.code_retriever is not None, "Code retriever is not initialized."
            contexts = "The Definition of this function is as below:\n"
            contexts += self.code_retriever.get_symbol_definition(function_name)
            contexts += "\n"

        if self.benchcfg.driver_flag:
            assert self.code_retriever is not None, "Code retriever is not initialized."
            contexts += "The Driver example from the same project is as below:\n"
            driver_list = self.code_retriever.get_all_driver_examples()
            for _, driver_source in driver_list:
                # only add unique driver examples
                if function_name not in driver_source:
                    contexts += driver_source + "\n"
                    break 

        # add other contexts
        prompt_template = prompt_template.format(header_files=header_string, function_usage=function_usage, contexts=contexts,
                                             function_signature=self.function_signature, tool_prompt=self.tool_prompt)

        # comment the prompt template
        prompt_template = "// " + prompt_template.replace("\n", "\n// ") + "\n"
        # save the prompt template to file
        save_code_to_file(prompt_template, self.save_dir / "prompt.txt")

        return prompt_template

    def compile_router_mapping(self, state: dict[str, Any]) -> str:
        last_message = state["messages"][-1]

        # print(messages)
        if last_message.content == CompileResults.Success.value:
            return self.FuzzerNode
        elif last_message.content in [CompileResults.CodeError.value, CompileResults.LinkError.value, 
                                      CompileResults.MissingHeaderError.value, CompileResults.IncludeError.value]:
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
        
    def fuzzer_router_mapping(self, state:dict[str, Any]) -> str:
        last_message = state["messages"][-1]

        # print(messages)
        if last_message.content.startswith(ValResult.NoError.value):
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

    def fill_prompt(self, prompt_template: str, **kwargs: dict[str, str]) -> str:
        for key, value in kwargs.items():
            prompt_template = prompt_template.replace(f"{{{key}}}", value) # type: ignore
        return prompt_template

    def load_model(self) -> BaseChatModel:

        if self.benchcfg.model_name.startswith("gpt"):
            if "gpt-5-mini" in self.benchcfg.model_name:
                llm = ChatOpenAI(model=self.benchcfg.model_name)
            else:
                llm = ChatOpenAI(model=self.benchcfg.model_name, temperature=self.benchcfg.temperature)
        elif self.benchcfg.model_name.startswith("anthropic"):
            llm = ChatOpenAI(
                api_key=os.getenv("OPENROUTER_API_KEY", ""), # type: ignore
                base_url="https://openrouter.ai/api/v1",
                model=self.benchcfg.model_name,
                temperature=self.benchcfg.temperature,
                 extra_body={
                    "reasoning": {
                        "enabled": self.benchcfg.reasoning,       # enables reasoning
                        "max_tokens": 2000     # reasoning token budget
                    },
                     "strict": False
                 }
                )

    # }
        else:
            llm = ChatOpenAI(
                api_key=os.getenv("OPENROUTER_API_KEY", ""), # type: ignore
                base_url="https://openrouter.ai/api/v1",
                model=self.benchcfg.model_name,
                temperature=self.benchcfg.temperature,
                # disabled_params={"parallel_tool_calls": None}
                
                )
            # from langchain_ollama import ChatOllama
            # llm = ChatOllama(model=self.benchcfg.model_name, temperature=self.benchcfg.temperature, base_url=self.benchcfg.base_url, reasoning=self.benchcfg.reasoning) 
            # llm_extract = ChatOllama(model=self.benchcfg.model_name, temperature=self.benchcfg.temperature, base_url=self.benchcfg.base_url, reasoning=False) 
        return llm
    
    def load_tools(self) -> list[StructuredTool]:

        assert self.code_retriever is not None, "Code retriever is not initialized."
        header_tool = StructuredTool.from_function(  # type: ignore
                func=self.code_retriever.get_symbol_header_tool,
                name="get_symbol_header_tool",
                description=self.code_retriever.get_symbol_header.__doc__,
            )
        definition_tool = StructuredTool.from_function( # type: ignore
            func=self.code_retriever.get_symbol_definition_tool,
            name="get_symbol_definition_tool",
            description=self.code_retriever.get_symbol_definition.__doc__,
        )

        declaration_tool = StructuredTool.from_function( # type: ignore
            func=self.code_retriever.get_symbol_declaration_tool,
            name="get_symbol_declaration_tool",
            description=self.code_retriever.get_symbol_declaration.__doc__,
        )
        view_tool = StructuredTool.from_function( # type: ignore
            func=self.code_retriever.view_code,
            name="view_code",
            description=self.code_retriever.view_code.__doc__,
        )

        struct_tool = StructuredTool.from_function(  # type: ignore
            func=self.code_retriever.get_struct_related_functions_tool,
            name="get_struct_related_functions_tool",
            description=self.code_retriever.get_struct_related_functions.__doc__,
        )
        reference_tool = StructuredTool.from_function(  # type: ignore
            func=self.code_retriever.get_symbol_references_tool,
            name="get_symbol_references_tool",
            description=self.code_retriever.get_symbol_references.__doc__,
        )

        location_tool = StructuredTool.from_function(  # type: ignore
            func=self.code_retriever.get_file_location_tool,
            name="get_file_location_tool",
            description=self.code_retriever.get_file_location_tool.__doc__,
        )

        # this tool should not be used for LLM4FDG benchmark since the functions are from the driver examples
        driver_tool = StructuredTool.from_function(  # type: ignore
            func=self.code_retriever.get_driver_example_tool,
            name="get_driver_example_tool",
            description=self.code_retriever.get_driver_example_tool.__doc__,
        )

        # add tools
        tools = []
        if self.benchcfg.fixing_mode == "agent":
            tools:list[StructuredTool] = [header_tool, definition_tool, declaration_tool, view_tool, location_tool, struct_tool, driver_tool, reference_tool]
        if self.benchcfg.header_mode == "agent" and self.benchcfg.fixing_mode != "agent":
            self.logger.info("Using agent mode for header files, add the header tool")
            tools:list[StructuredTool] = [header_tool]
        
        # remove the some tools for Java
        if self.project_lang == LanguageType.JAVA:
            tools =  [definition_tool, view_tool, location_tool, driver_tool, reference_tool]
        return tools

    
    def build_graph(self) -> StateGraph:

        assert self.code_retriever is not None, "Code retriever is not initialized."
        llm_extract = ChatOpenAI(model="gpt-4.1-mini")
        llm = self.load_model()

        # code formatter
        llm_code_extract: BaseChatModel = llm_extract.with_structured_output(CodeAnswerStruct) # type: ignore
        code_formater = CodeFormatTool(llm_code_extract, load_prompt_template(f"{PROJECT_PATH}/agent/prompts/extract_code.txt"))

        tools = self.load_tools()
        if len(tools) > 0:
            tool_llm: BaseChatModel = llm.bind_tools(tools) # type: ignore
        else:
            tool_llm = llm

        draft_responder = HarnessGenerator(tool_llm, self.benchcfg.max_tool_call, continue_flag=True, save_dir=self.save_dir, 
                                        code_callback=code_formater.extract_code, logger=self.logger, model_name=self.benchcfg.model_name)


        compile_fix_prompt = load_prompt_template(f"{PROJECT_PATH}/agent/prompts/compile_prompt.txt")
        fuzz_fix_prompt = load_prompt_template(f"{PROJECT_PATH}/agent/prompts/fuzzing_prompt.txt")

        local_compile_fix_prompt =  self.fill_prompt(compile_fix_prompt, tool_prompt=self.tool_prompt,   # type: ignore
                                                     function_signature=self.function_signature)   # type: ignore
        local_fuzz_fix_prompt = self.fill_prompt(fuzz_fix_prompt, tool_prompt=self.tool_prompt,  # type: ignore
                                                 function_signature=self.function_signature)  # type: ignore

        if self.benchcfg.fixing_mode == "oss_fuzz":
            prompt_builder = OSSFUZZFixerPromptBuilder
        elif self.benchcfg.fixing_mode == "issta":
            prompt_builder = ISSTAFixerPromptBuilder
        else:
            prompt_builder = FixerPromptBuilder

        fix_builder = prompt_builder(self.benchcfg, self.oss_fuzz_benchmark, self.project_name, self.new_project_name, self.code_retriever, self.logger,
                                        local_compile_fix_prompt, local_fuzz_fix_prompt, self.project_lang)

        code_fixer = CodeFixer(tool_llm, self.benchcfg.max_fix, self.benchcfg.max_tool_call,  self.save_dir, self.benchcfg.cache_root,
                                 code_callback=code_formater.extract_code, logger=self.logger, model_name=self.benchcfg.model_name)

        fuzzer = Validation(self.benchcfg.oss_fuzz_dir, self.new_project_name, self.project_lang, 
                             self.benchcfg.run_time,  self.save_dir,  self.logger)
        
        if self.benchcfg.header_mode == "all":
            # use the header compiler wrapper
            self.logger.info("Using HeaderCompilerWraper for compiling")
            compiler = HeaderCompilerWraper(self.benchcfg.oss_fuzz_dir, self.project_name, self.new_project_name, self.code_retriever, 
                                            self.project_lang, self.harness_pairs, self.save_dir, self.benchcfg.cache_root, self.logger)
        else:
            compiler = CompilerWraper(self.benchcfg.oss_fuzz_dir, self.benchcfg.benchmark_dir, self.project_name, self.new_project_name,
                                       self.code_retriever, self.project_lang, self.harness_pairs, self.benchcfg.compile_enhance,
                                         self.save_dir, self.benchcfg.cache_root, self.logger, function_signature=self.function_signature if self.is_static else "")
       
        checker = SemaCheckNode(self.benchcfg.oss_fuzz_dir, self.benchcfg.benchmark_dir, self.project_name, self.new_project_name, self.function_signature, self.project_lang, self.benchcfg.semantic_mode, self.logger)

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

        if self.benchcfg.memory_flag:
            memory = MemorySaver()
            # the path map is mandatory
            graph: StateGraph = builder.compile(memory) # type: ignore
        else:
            graph: StateGraph = builder.compile()  # type: ignore
        return graph


    def run_graph(self, graph: StateGraph) -> None:
        
        # read prompt according to the project language (extension of the harness file)
        ext_lang = self.oss_tool.get_extension(None)
        if  ext_lang in [LanguageType.CPP, LanguageType.C, LanguageType.JAVA]:
            generator_prompt_template = load_prompt_template(f"{PROJECT_PATH}/agent/prompts/{ext_lang.value.lower()}prompt.txt")
        else:
            raise ValueError(f"Unsupported language for harness generation: {ext_lang}") 
        
        # build the prompt for initial generator
        generator_prompt = self.build_init_prompt(generator_prompt_template)

        # plot_graph(graph)
        config = {"configurable": {"thread_id": "1"}, "recursion_limit": 200} # type: ignore
        events = graph.stream( # type: ignore
            {"messages": [("user", generator_prompt)], "function_signature": self.function_signature},
            config,
            stream_mode="values",
        )

        with open(os.path.join(self.save_dir, "output.log"), "w") as f:
            for i, step in enumerate(events): # type: ignore
                f.write(f"Step {i}\n")  # Save step number if needed
                output = io.StringIO()  # Create an in-memory file-like object
                with contextlib.redirect_stdout(output):  # Capture print output
                    if step["messages"][-1].type != "tool": # type: ignore
                        step["messages"][-1].pretty_print()  # type: ignore
                    else:
                        for msg in step["messages"][::-1]:  # type: ignore
                            if msg.type != "tool":  # type: ignore
                                break
                            msg.pretty_print()  # type: ignore
                f.write(output.getvalue() + "\n")  # Write captured output to file

                f.flush()