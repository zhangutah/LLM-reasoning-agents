import os
from constants import EvalResult
from collections import defaultdict
import pickle
from agent_tools.code_tools.parsers.cpp_parser import CPPParser
# from agent_tools.code_tools.parsers.c_parser import CParser
from utils.misc import extract_name
from pathlib import Path
from typing import DefaultDict

OSSFUZZ = Path("/home/yk/code/oss-fuzz")
def get_language_info(project_name: str) -> str:

    yaml_file = OSSFUZZ / "projects" / project_name / "project.yaml"
    if yaml_file.exists():
        with open(yaml_file, "r") as f:
            import yaml
            cfg = yaml.safe_load(f)
            lang = cfg.get("language", "none")
            return lang
    return "none"

def get_run_res(work_dir: Path, semantic_mode: str="eval") -> tuple[EvalResult, bool]:

    work_dir = Path(work_dir)
      # read the agent.log
    log_file = work_dir / "agent.log"
    harness_path = work_dir / "harness.txt"
    func_sig_path = work_dir / "function.txt"

    function_signature = func_sig_path.read_text()
    function_name = extract_name(function_signature)

    if not log_file.exists():
        return EvalResult.NoLogError, False
    
    log_lines = log_file.read_text()
   
    # count the no usage case
    if "Found 0 usage" in log_lines:
        usage_flag = False
    else:
        usage_flag = True

    for line in log_lines.split("\n"):
        if "WARNING" in line and "Exit" in line:
            return EvalResult.NoLogError, usage_flag

    # for issta
    if semantic_mode in ["both", "eval"]:
        pass_pattern = "Semantic check passed"
    else:
        pass_pattern = "NoError"
        
    if pass_pattern not in log_lines:
        return EvalResult.Failed, usage_flag
    
    if semantic_mode == "eval" and "Semantic check failed" in log_lines:
        return EvalResult.Failed, usage_flag

    parser = CPPParser(file_path=harness_path)

    if parser.exist_function_definition(function_name):
        return EvalResult.Fake, usage_flag
    
    if parser.is_fuzz_function_called(function_name):
        return EvalResult.Success, usage_flag
    else:
        return EvalResult.NoCall, usage_flag
 
def run_agent_res(output_path: Path, semantic_mode:str, n_run:int=1): 

    res_count: DefaultDict[str, int] = defaultdict(int)
    lang_count: DefaultDict[str, int] = defaultdict(int)
    output_path = Path(output_path)
    success_name_list:list[str] = []
    failed_name_list:list[str] = []

    res_file = output_path / f"res_{n_run}.txt"
    # with open(res_file, "w") as save_f:
    
    # save the projects whose functions are all failed

    build_failed_functions: list[str] = []
    failed_projects: set[str] = set()
    all_path:list[tuple[str, str, Path]] = []
    # sort the directory
    for project_path in sorted(output_path.iterdir()):
        if not project_path.is_dir():
            continue

        for function_path in project_path.iterdir():
            if not function_path.is_dir():
                continue

            for work_dir in function_path.iterdir():
                if not work_dir.is_dir():
                    continue
                if not work_dir.exists():
                    continue
               
                  # get the run number
                if "run" not in work_dir.name:
                    continue
                n = int(work_dir.name.split("_")[0][3:])
                if n > n_run:
                    continue
                # check if the directory is empty
                if len(os.listdir(work_dir)) <= 4:
                    build_failed_functions.append(f"{project_path.name}/{function_path.name}")
                    continue
              
                all_path.append((project_path.name, function_path.name, work_dir))
                failed_projects.add(project_path.name)
        
    with open(res_file, "w") as save_f:
        for project_name, function_name, work_dir in all_path:
          
            eval_res, usage_falg = get_run_res(work_dir, semantic_mode=semantic_mode)

            if eval_res != EvalResult.Success:
                if function_name not in failed_name_list:
                    failed_name_list.append(function_name)
                    res_count[eval_res.value] += 1
                    save_f.write(f"{project_name}/{function_name}. fuzz res: {eval_res}\n")
                continue

            if project_name in failed_projects:
                failed_projects.remove(project_name)
            
            #  only count once
            if function_name in success_name_list:
                continue

            res_count[eval_res.value] += 1
            success_name_list.append(function_name)

            # get language info
            lang = get_language_info(project_name)
            lang_count[lang] += 1
            if usage_falg:
                save_f.write(f"{project_name}/{function_name}. fuzz res: {eval_res}, HaveUsage\n")
            else:
                save_f.write(f"{project_name}/{function_name}. fuzz res: {eval_res}, NoUsage\n")
        
        save_f.write(f"Results count: {res_count}\n")
        save_f.write(f"Success:{res_count[EvalResult.Success.value] }\n")
        save_f.write(f"Language success count: {lang_count}\n")

    with open(os.path.join(output_path, f"failed_projects_{n_run}.txt"), "w") as f:
        for project in failed_projects:
            f.write(f"{project}\n")

    with open(os.path.join(output_path, f"build_failed_functions_{n_run}.txt"), "w") as f:
        for func in build_failed_functions:
            f.write(f"{func}\n")

    pickle.dump(success_name_list, open(os.path.join(output_path, f"success_name_{n_run}.pkl"), "wb"))


if __name__ == "__main__":

    run_agent_res(Path("/home/yk/code/LLM-reasoning-agents/outputs_wild/gpt5-mini/raw/"), semantic_mode="eval", n_run=1)
    # run_oss_fuzz_res()