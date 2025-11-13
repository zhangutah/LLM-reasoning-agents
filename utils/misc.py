import os
from matplotlib import pyplot as plt
import io
import yaml
from collections import defaultdict
from constants import PROJECT_PATH, FuzzEntryFunctionMapping,  LanguageType
from langgraph.graph import StateGraph # type: ignore
import re
import random
from typing import DefaultDict, Any, Optional
from pathlib import Path
from tree_sitter import Language, Parser
import tree_sitter_cpp

def filter_examples(project_code_usage: list[dict[str, str]], project_lang: LanguageType, usage_token_limit:int=200) -> str:
    filter_code_usage: list[dict[str, str]] = []
    for code in project_code_usage:
        if FuzzEntryFunctionMapping[project_lang] in code["source_code"]:
            continue
        # token limit
        if len(code["source_code"].split()) > usage_token_limit:
            continue
        filter_code_usage.append(code)

    if len(filter_code_usage) == 0:
        function_usage = ""
    else:
        # randomly select one usage
        random_index = random.randint(0, len(filter_code_usage) - 1)
        function_usage = filter_code_usage[random_index]["source_code"]
    
    return function_usage


# def extract_name(function_signature: str, keep_namespace: bool=False)-> str:
#     # Remove the parameters by splitting at the first '('
#     function_name = function_signature.split('(')[0]
#     # Split the function signature into tokens to isolate the function name
#     tokens = function_name.strip().split()
#     assert len(tokens) > 0

#     # The function name is the last token, this may include namespaces ::
#     function_name = tokens[-1]

#     if not keep_namespace:
#         # split the function name by <
#         if "<" in function_name:
#             function_name = function_name.split("<")[0]

#         # split the function name by ::
#         if "::" in function_name:
#             function_name = function_name.split("::")[-1]

#     # remove * from the function name
#     if function_name.startswith("*"):
#         function_name = function_name.replace("*", "")

#     return function_name

def _strip_templates(name: str) -> str:
    """Remove all balanced < … > template arguments, preserving :: separators."""
    out:list[str] = [] 
    depth = 0
    for ch in name:
        if ch == '<':
            depth += 1                      # enter template-argument list
        elif ch == '>':
            depth -= 1                      # leave   "
        elif depth == 0:
            out.append(ch)                  # keep only when *not* inside <>
    return ''.join(out).replace(' ', '')    # also drop stray spaces

def extract_name(function_signature: str, keep_namespace: bool=False, exception_flag: bool=True)-> str:

    if  "N/A" in function_signature:
        return "N/A"
    lang = Language(tree_sitter_cpp.language())
    parser = Parser(lang)

    function_signature = function_signature.strip()
    if not function_signature.endswith(";"):
        function_signature += ";"
    # a patch, replace a
    function_signature = function_signature.replace("(anonymous namespace)::", "") # type: ignore
    
    # Parse the function signature
    # Note: The parser expects a byte string, so we encode the string to bytes
    tree = parser.parse( function_signature.encode('utf-8'))

    # Find the function declaration node
    query_str = """
    (function_declarator
        [
            (qualified_identifier)@function_name
            (identifier) @function_name
        ]
    )
    """
    query = lang.query(query_str)
    captures = query.captures(tree.root_node)
    if not captures:
        if exception_flag: 
            raise ValueError(f"Function signature '{function_signature}' does not contain a valid function declaration.")
        else:
            return ""
    if len(captures) > 1:
        if exception_flag:
            raise ValueError(f"Function signature '{function_signature}' contains multiple function declarations, expected only one.")
        else:
            return ""

    full_name = captures["function_name"][0]
    
    # remove templates
    stripped_name = _strip_templates(full_name.text.decode('utf-8')) # type: ignore
    if not keep_namespace:
        # split the function name by :: and remove the namespace
        if "::" in stripped_name:
            stripped_name = stripped_name.split("::")[-1]
    return stripped_name
            

def save_code_to_file(code: str, file_path: Path) -> None:
    '''Save the code to the file'''

    dirname = file_path.parent
    if not dirname.exists():
        dirname.mkdir(parents=True, exist_ok=True)

    file_path.write_text(code, encoding="utf-8")


def plot_graph(graph: Any, save_flag: bool = True) -> None: 
    # Assuming graph.get_graph().draw_mermaid_png() returns a PNG image file path
    image_data = graph.get_graph().draw_mermaid_png()

    # Use matplotlib to read and display the image
    img = plt.imread(io.BytesIO(image_data)) # type: ignore
    plt.axis('off')  # type: ignore

    if save_flag:
        plt.savefig("graph.png") # type: ignore
    else:
        plt.imshow(img) # type: ignore
        plt.show()  # type: ignore



def remove_color_characters(text: str) -> str:
      # remove color characters
    ansi_escape = re.compile(r'\x1b\[[0-9;]*m')
    return ansi_escape.sub('', text)


def load_prompt_template(template_path: str) -> str:
    '''Load the prompt template'''
    with open(template_path, 'r') as file:
        return file.read()


def add_lineno_to_code(code: str, start_lineno: int) -> str:
    """
    Add line numbers to the code string.
    Args:
        code (str): The source code to add line numbers to.
        lineno (int): The starting line number (0-indexed).
    Returns:
        str: The code with line numbers added as comments.
    """
    numbered_code = ""
    for i, line in enumerate(code.splitlines()):
        numbered_code += f"// {start_lineno + i}: {line}\n"
    return numbered_code


# def load_model_by_name(model_name: str, temperature: float = 0.7) -> BaseChatModel:
#     '''Load the model by name'''

#     #  obtain environment variables
#     DEEPSEEK_API_KEY = os.getenv("DEEPSEEK_API_KEY")

#     name_vendor_mapping = {
#         "gpt-4o":"openai",
#         "gpt-40-mini":"openai",
#         "gpt-4o-turbo":"openai",
#         "gemini-2.0-flash-exp":  "google",
#         "gemini-1.5-flash": "google",
#         "deepseekv3": "deepseek",
#     }
#     assert model_name in name_vendor_mapping.keys()

#     vendor_name = name_vendor_mapping.get(model_name)
#     if vendor_name == "openai":
#         return ChatOpenAI(model_name, temperature=temperature)
#     elif vendor_name == "deepseek":
#         assert DEEPSEEK_API_KEY is not None
#         return ChatOpenAI(model='deepseek-chat', openai_api_key=DEEPSEEK_API_KEY, openai_api_base='https://api.deepseek.com')
#     elif vendor_name == "anthropic":
#         return ChatAnthropic(model_name, temperature=temperature)
#     elif vendor_name == "google":
#         return ChatGoogleGenerativeAI(model_name, temperature=temperature)
#     else:
#         return None
    

def function_statistics():

    # read benchmark names
    bench_dir = os.path.join(PROJECT_PATH, "benchmark-sets", "all")

    function_list:list[int] = []
    for file in os.listdir(bench_dir):
        # read yaml file
        with open(os.path.join(bench_dir, file), 'r') as f:
            data = yaml.safe_load(f)
            # project_name = data.get("project")
            lang_name = data.get("language")
            # project_harness = data.get("target_path")

            if lang_name not in ["c++", "c"]:
                continue
        
            n_function = len(data.get("functions"))
            function_list.append(n_function)
    print(f"Total number of projects: {len(function_list)}")
   
    total_func = 0
    for i in range(1, 6):
        print(f"{i} of functions in {i} projects: {function_list.count(i)}")
        total_func += i * function_list.count(i)
  
    print(f"Total number of functions: {total_func}")



def project_statistics():

    # read benchmark names
    bench_dir = os.path.join(PROJECT_PATH, "benchmark-sets", "all")

    all_projects: list[tuple[str, str, str]] = []
    for file in os.listdir(bench_dir):
        # read yaml file
        with open(os.path.join(bench_dir, file), 'r') as f:
            data = yaml.safe_load(f)
            project_name = data.get("project")
            lang_name = data.get("language")
            project_harness = data.get("target_path")

            all_projects.append((project_name, lang_name, project_harness))


    # open another file
    build_res_file = os.path.join(PROJECT_PATH, "prompts", "res.txt")

    build_res = {} 
    with open(build_res_file, 'r') as f:
        for line in f:
            project_name, res = line.split(";")
            build_res[project_name] = res

    lang_count: DefaultDict[str, int] = defaultdict(int)

    for project_name, lang_name, project_harness in all_projects:

        if "Error" in build_res[project_name]:
            print(f"{project_name} {build_res[project_name]}")
            # remove from benchmark 
            # file_path = os.path.join(bench_dir, f"{project_name}.yaml")
            # os.remove(file_path)

            continue

        lang_count[lang_name] += 1

    print(lang_count)


def get_benchmark_functions(bench_dir: Path, allowed_projects:list[str] = [], 
                            allowed_langs: list[str]=[], allowed_functions: list[str] = [], funcs_per_project: int=1) -> dict[str, list[str]]:
    """Get all functions from the benchmark directory."""

    allowed_names: list[str] = []
    # not None or empty
    if allowed_functions:
        for function_signature in allowed_functions:
            function_name = extract_name(function_signature, keep_namespace=True)
            allowed_names.append(function_name)
        
    function_dict: dict[str, list[str]] = {}
    # read benchmark names
    all_files = os.listdir(bench_dir)
    all_files.sort()

    for file in all_files:
        # read yaml file
        with open(os.path.join(bench_dir, file), 'r') as f:
            data = yaml.safe_load(f)
            project_name = data.get("project")
            lang_name = data.get("language")

            # only allow specific projects
            if allowed_projects and project_name not in allowed_projects:
                continue
        
            if allowed_langs and lang_name not in allowed_langs:
                continue
        
            count = 0
            function_list: list[str] = []
            for function in data.get("functions"):
                if "signature" not in function.keys():
                    print(f"Function signature not found in {project_name} {function['name']}")
                    continue
                function_signature = function["signature"]
                function_name = extract_name(function_signature, keep_namespace=True)
                
                # screen the function name
                if len(allowed_names) > 0 and function_name not in allowed_names:
                    continue
                if count >= funcs_per_project:
                    break
                function_list.append(function_signature)
                count += 1

            if len(function_list) != 0:
                function_dict[project_name] = function_list
    return function_dict

import json
from langchain_core.messages import AIMessage, ToolCall
def fix_qwen_tool_calls(res: Any) -> Optional[AIMessage]:

    try:
        tool_calls: list[ToolCall] = []  # Accumulate valid tool calls
        for invalid_call in res.invalid_tool_calls:
            function_name = invalid_call['name']
            args_str = invalid_call['args']
            # Split the concatenated JSON strings
            args_list = re.findall(r'\{[^}]*\}', args_str)
            
            # Process each JSON string
            for arg_json in args_list:
                try:
                    args = json.loads(arg_json)
                    # Create a ToolCall object
                    tool_call = ToolCall(
                        id=invalid_call['id'],  # Use the original ID
                        name=function_name,
                        args=args
                    )
                    tool_calls.append(tool_call)

                except json.JSONDecodeError as e:
                    print(f"Failed to parse JSON: {e}")
                except Exception as e:
                    print(f"Error calling tool: {e}")
            
            # Create a synthetic AIMessage with the corrected tool calls
            synthetic_ai_message = AIMessage(content=res.content, tool_calls=tool_calls)
            
            return synthetic_ai_message

    except Exception as e:
        print(f"Error in fix_qwen_tool_calls: {e}")
        return None

def fix_claude_tool_calls(res: Any) -> Optional[AIMessage]:
    """
    Fix Claude tool calls when parameters are None instead of empty JSON object.
    Claude sometimes passes None as args when tools don't require parameters,
    but the JSON parser expects a valid JSON string.
    """
    try:
        tool_calls: list[ToolCall] = []  # Accumulate valid tool calls
        for invalid_call in res.invalid_tool_calls:
            function_name = invalid_call['name']
            call_id = invalid_call['id']
            args_value = invalid_call['args']
            
            # Handle None args by using empty dict
            if args_value is None:
                args = {}
            else:
                # Try to parse the args as JSON if it's a string
                try:
                    if isinstance(args_value, str):
                        args = json.loads(args_value)
                    else:
                        # If it's already a dict or other object, use it directly
                        args = args_value if args_value is not None else {}
                except json.JSONDecodeError:
                    # If JSON parsing fails, use empty dict
                    print(f"Failed to parse args for tool {function_name}: {args_value}")
                    args = {}
            
            # Create a ToolCall object with fixed args
            tool_call = ToolCall(
                id=call_id,
                name=function_name,
                args=args
            )
            tool_calls.append(tool_call)
        
        # Create a synthetic AIMessage with the corrected tool calls
        synthetic_ai_message = AIMessage(content=res.content, tool_calls=tool_calls)
        return synthetic_ai_message

    except Exception as e:
        print(f"Error in fix_claude_tool_calls: {e}")
        return None
    
    
def logger_wrapper(logger: Optional[Any], msg: str, level: str = "info") -> None:
    """A wrapper for logging messages."""
    if logger:
        if level == "info":
            logger.info(msg)
        elif level == "error":
            logger.error(msg)
        elif level == "warning":
            logger.warning(msg)
        elif level == "debug":
            logger.debug(msg)
        else:
            logger.info(msg)
    else:
        print(msg)


def write_list_to_file(item_list: list[str], file_path: Path) -> None:
    """Write a list of items to a file, one item per line."""
    with open(file_path, 'w') as f:
        for item in item_list:
            f.write(f"{item}\n")

def get_ext_lang(file_path: Path) -> Optional[LanguageType]:
    """Get the programming language from the file extension."""
    ext = file_path.suffix.lower()
    ext_lang_mapping = {
        ".c": LanguageType.C,
        ".c++": LanguageType.CPP,
        ".cpp": LanguageType.CPP,
        ".cc": LanguageType.CPP,
        ".cxx": LanguageType.CPP,
        ".java": LanguageType.JAVA,
    }
    return ext_lang_mapping.get(ext, None)

def kill_process(process: Any) -> None: 
    try:
        if process and process.poll() is None:
            process.kill()
            process.wait(timeout=5)
    except:
        pass      


def is_empty_json_file(json_path: Path) -> bool:
    """Check if a JSON file is empty or contains an empty list/dict."""
    if not json_path.exists():
        return True

    try:
        with open(json_path, 'r') as f:
            data = json.load(f)
            if not data:  # Check for empty list or dict
                return True
            return False
    except json.JSONDecodeError:
        return True
if __name__ == "__main__":

    with open("/home/yk/code/LLM-reasoning-agents/benchmark-sets/ntu/gdk-pixbuf.yaml", 'r') as f:
        data = yaml.safe_load(f)
        project_name = data.get("project")
        print(f"Project name: {project_name}")
    # function_statistics()