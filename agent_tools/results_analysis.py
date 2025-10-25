import os
from constants import EvalResult
from collections import defaultdict
from agent_tools.code_tools.parsers.cpp_parser import CPPParser
# from agent_tools.code_tools.parsers.c_parser import CParser
from utils.misc import extract_name
from pathlib import Path
from typing import DefaultDict
from utils.misc import write_list_to_file
from typing import Any
import json

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

def get_run_res(work_dir: Path, semantic_mode: str="eval") -> EvalResult:

    work_dir = Path(work_dir)
      # read the agent.log
    log_file = work_dir / "agent.log"
    harness_path = work_dir / "harness.txt"
    func_sig_path = work_dir / "function.txt"

    function_signature = func_sig_path.read_text()
    function_name = extract_name(function_signature)

    if not log_file.exists():
        return EvalResult.NoLogError

    log_lines = log_file.read_text()
   
    for line in log_lines.split("\n"):
        if "WARNING" in line and "Exit" in line:
            return EvalResult.NoLogError

    # for issta
    if semantic_mode in ["both", "eval"]:
        pass_pattern = "Semantic check passed"
    else:
        pass_pattern = "NoError"
        
    if pass_pattern not in log_lines:
        return EvalResult.Failed
    
    if semantic_mode == "eval" and "Semantic check failed" in log_lines:
        return EvalResult.Failed

    parser = CPPParser(file_path=harness_path)

    if parser.exist_function_definition(function_name):
        return EvalResult.Fake
    
    if parser.is_fuzz_function_called(function_name):
        return EvalResult.Success
    else:
        return EvalResult.NoCall

def collect_run_info(output_path: Path, n_run:int=1, single_run:bool=False) -> tuple[list[tuple[str, str, Path]], list[str], set[str]]:
    build_failed: list[str] = []
    all_projects: set[str] = set()
    all_path:list[tuple[str, str, Path]] = []
    # sort the directory
    for project_path in sorted(output_path.iterdir()):
        if not project_path.is_dir():
            continue
        all_projects.add(project_path.name)

        for function_path in project_path.iterdir():
            if not function_path.is_dir():
                continue

            for work_dir in function_path.iterdir():
                if not work_dir.is_dir():
                    continue
                if not work_dir.exists():
                    continue
               
                # get the run number
                n = int(work_dir.name.split("_")[0][3:])
                if n > n_run:
                    continue
                if single_run and n != n_run:
                    continue

                # check if the directory is empty
                if len(os.listdir(work_dir)) <= 4:
                    build_failed.append(f"{project_path.name}/{function_path.name}")
                    continue
                func_sig_path = work_dir / "function.txt"
                if not func_sig_path.exists():
                    build_failed.append(f"{project_path.name}/{function_path.name}")
                    continue
                func_sig = func_sig_path.read_text().strip()

                all_path.append((project_path.name, func_sig, work_dir))

    return all_path, build_failed, all_projects

def run_agent_res(output_path: Path, semantic_mode:str, n_run:int=1): 

    res_count: DefaultDict[str, int] = defaultdict(int)
    lang_count: DefaultDict[str, int] = defaultdict(int)
    output_path = Path(output_path)
    success_json:dict[str, Any] = {}

    res_file = output_path / f"res_{n_run}.txt"
    
    # save the projects whose functions are all failed
    all_path, build_failed, all_projects = collect_run_info(output_path, n_run=n_run)
    with open(res_file, "w") as save_f:
        for project_name, func_sig, work_dir in all_path:
            function_name = extract_name(func_sig, keep_namespace=True)
            eval_res = get_run_res(work_dir, semantic_mode=semantic_mode)

            if eval_res != EvalResult.Success:
                res_count[eval_res.value] += 1
                save_f.write(f"{project_name}/{function_name}. fuzz res: {eval_res}\n")
                continue

            if project_name in all_projects:
                all_projects.remove(project_name)
            
            #  only count once
            res_count[eval_res.value] += 1

            func_dict = {
                "project": project_name,
                "work_dir": str(work_dir),
            }
            success_json[func_sig] = func_dict

            # get language info
            lang = get_language_info(project_name)
            lang_count[lang] += 1
            save_f.write(f"{project_name}/{function_name}. fuzz res: {eval_res}\n")
        
        save_f.write(f"Results count: {res_count}\n")
        save_f.write(f"Success:{res_count[EvalResult.Success.value] }\n")
        save_f.write(f"Language success count: {lang_count}\n")

    all_projects = sorted(list(all_projects))
    write_list_to_file(all_projects, output_path / f"failed_projects_{n_run}.txt")
    write_list_to_file(build_failed, output_path / f"build_failed_functions_{n_run}.txt")
    # json dump
    with open(output_path / f"success_functions_{n_run}.json", "w") as f:
            json.dump(success_json, f, indent=4)


def get_evaluation_results(eval_path: Path):
    
    for project_path in sorted(eval_path.iterdir()):
        if not project_path.is_dir():
            continue

        for function_path in project_path.iterdir():
            if not function_path.is_dir():
                continue

            for run_dir in function_path.iterdir():
                if not run_dir.is_dir():
                    continue

                cov_file = run_dir / "cov.txt"
                if not cov_file.exists():
                    continue

                # cov_lines = cov_file.read_text().split("\n")
                # init_cov = 0
                # final_cov = 0
                # for line in cov_lines:
                #     if "Initial coverage" in line:
                #         init_cov = int(line.split(":")[-1].strip())
                #     elif "Final coverage" in line:
                #         final_cov = int(line.split(":")[-1].strip())
                
                # print(f"{project_path.name}/{function_path.name}: Initial coverage: {init_cov}, Final coverage: {final_cov}")

if __name__ == "__main__":

    run_agent_res(Path("/home/yk/code/LLM-reasoning-agents/outputs_wild/gpt5-mini/issta"), semantic_mode="eval", n_run=1)
    # run_oss_fuzz_res()