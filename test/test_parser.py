from pathlib import Path
import json
from constants import LSPFunction, LanguageType, Retriever
from utils.docker_utils import DockerUtils
import random
import shutil
import os



def find_empty_symbol(oss_fuzz_dir: Path, cache_dir: Path, project_name: str = "bind9") -> None:


    random_str = ''.join(random.choices("abcdefghijklmnopqrstuvwxyz", k=16))
    new_project_name = "{}_{}".format(project_name, random_str)
    
    scr_path = oss_fuzz_dir / "projects" / project_name
    dst_path = oss_fuzz_dir / "projects" / new_project_name
    shutil.copytree(scr_path, dst_path, dirs_exist_ok=True)

    docker_tool = DockerUtils(oss_fuzz_dir, project_name, new_project_name, LanguageType.C)

    docker_tool.build_image(["python", os.path.join(oss_fuzz_dir, "infra", "helper.py"),
                            "build_image", new_project_name, "--pull", "--cache"])

    proejct_cache_dir = cache_dir / project_name

    symbol_set: set[tuple[str, str]] = set()
    for file in proejct_cache_dir.iterdir():

        if file.suffix != '.json':
            continue
        with open(file, 'r') as f:
           json_data = json.load(f)
        if json_data:
            continue

        # delete this file if the json data is empty
        os.remove(file)
        # If the JSON data is empty, print the file name
      
        # extract the symbol name from the file name
        symbol_name = file.stem
        retriever_method = Retriever.Parser
        lsp_func = ""
        for lsp_function in LSPFunction:
            if lsp_function.value in symbol_name:

                retriever_method = Retriever.LSP if Retriever.LSP.value in symbol_name else Retriever.Parser
                symbol_name = symbol_name.split(lsp_function.value)[0]
                lsp_func = lsp_function.value
                break
        symbol_name = symbol_name[:-1] if symbol_name.endswith('_') else symbol_name

        # grep the symbol name in the file
        # create the docker image name
        if retriever_method == Retriever.LSP:
            # see if the symbol can be retrieved by parser
            paraser_file = file.parent /  f"{symbol_name}_{lsp_func}_parser.json"
        else:
            paraser_file = file.parent /  f"{symbol_name}_{lsp_func}_lsp.json"

      
        grep_res = docker_tool.run_cmd("grep --binary-files=without-match -rw {}".format(symbol_name))
        if not grep_res.strip():
            continue

        # print(f"Symbol {symbol_name} found in the project")

        empty_flag = True
        if lsp_func not in  [LSPFunction.Definition.value, LSPFunction.Declaration.value]:
            symbol_set.add((symbol_name, lsp_func))
            continue
  
        for lsp_func in [LSPFunction.Definition.value, LSPFunction.Declaration.value]:
            for retriever in Retriever:
                paraser_file = file.parent /  f"{symbol_name}_{lsp_func}_{retriever.value}.json"

                if not paraser_file.exists():
                    continue
                
                with open(paraser_file, 'r') as pf:
                    parser_data = json.load(pf)
                # if the parser data is empty, add the symbol to the set
                if parser_data:
                    empty_flag = False

        # both definition and declaration are empty, add the symbol to the set
        if empty_flag:
            symbol_set.add((symbol_name, lsp_func))
    print(f"Processing project: {project_name}")
    print(f"Empty symbols found: {symbol_set}")
    try:        
        # first remove the out directory
        docker_tool.clean_build_dir()
        # remove the docker image here
        docker_tool.remove_image()
        # remove the project directory
        shutil.rmtree(oss_fuzz_dir / "projects" / new_project_name)
        # clean the build 
        shutil.rmtree(oss_fuzz_dir / "build" / "out" / new_project_name)
    except:
        pass

def test_namespace_identifier_matching():
    from agent_tools.code_tools.parsers.cpp_parser import CPPParser

    code = """
    #include <iostream>
    namespace A {
        void foo() {
            std::cout << "Hello, World!" << std::endl;
        }
    }

    extern "C" int LLVMFuzzerTestOneInput(const uint8_t *Data, size_t Size) {
        foo();
        return 0;
    }
    """

    parser = CPPParser(None, code)
    call_node = parser.is_fuzz_function_called("foo")
    assert call_node, "Function call node should not be None"
    # print("Function call node found:", call_node.text.decode("utf-8"))

    call_node_ns = parser.is_fuzz_function_called("B::foo")
    assert call_node_ns, "Function call node with namespace should not be None"
    # print("Function call node with namespace found:", call_node_ns.text.decode("utf-8"))

if __name__ == "__main__":

    test_namespace_identifier_matching()
    exit(0)
    cache_dir = Path("/home/yk/code/LLM-reasoning-agents/cache/")
    oss_fuzz_dir = Path("/home/yk/code/oss-fuzz/")

    for file in cache_dir.iterdir():
        if not file.is_dir():
            continue

        project_name = file.name
        find_empty_symbol(oss_fuzz_dir, cache_dir, project_name)