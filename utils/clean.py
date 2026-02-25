import os
from pathlib import Path    
import shutil
import json
from typing import Any

def remove_large_log_files(directory: str, size_limit_mb: int = 1):
    size_limit_bytes = size_limit_mb * 1024 * 1024

    for root, _, files in os.walk(directory):
        for file in files:
            if file.endswith('.log'):
                file_path = os.path.join(root, file)
                try:
                    if os.path.getsize(file_path) > size_limit_bytes:
                        print(f"Deleting: {file_path} (Size: {os.path.getsize(file_path)} bytes)")
                        os.remove(file_path)
                except Exception as e:
                    print(f"Error processing file {file_path}: {e}")

def remove_corpus_dir(directory: str):
    
    dir_path = Path(directory)

    for root, dirs, _ in os.walk(directory):
        for dir_name in dirs:
            if dir_name == "corpora":
                dir_path = os.path.join(root, dir_name)
                try:
                    shutil.rmtree(dir_path)
                    print(f"Deleted directory: {dir_path}")
                except Exception as e:
                    print(f"Error deleting directory {dir_path}: {e}")

def remove_def_cache(directory: str):
    for path in Path(directory).rglob('*.json'):
        if path.name.endswith('declaration_lsp.json') or path.name.endswith('definition_lsp.json') or path.name.endswith('declaration_parser.json') or path.name.endswith('definition_parser.json'):
            try:
                path.unlink()
                print(f"Deleted: {path}")
            except Exception as e:
                print(f"Error deleting {path}: {e}")

def remove_empty_cache(directory: str):
    for path in Path(directory).rglob('*.json'):
        
        with open(path, 'r') as f:
            content = f.read().strip()
            if content == "{}" or content == "[]":
                try:
                    path.unlink()
                    print(f"Deleted empty cache: {path}")
                except Exception as e:
                    print(f"Error deleting {path}: {e}")
      
def remove_failed_dir(directory: str):
    
    total_removed = 0
    project_list = os.listdir(directory)
    for project in project_list:
        project_dir = os.path.join(directory, project)
        if not os.path.isdir(project_dir):
            continue
        for func_name in os.listdir(project_dir):
            func_dir = os.path.join(project_dir, func_name)
            for run_name in os.listdir(func_dir):
                run_dir = os.path.join(func_dir, run_name)

                if not os.path.isdir(run_dir):
                    continue

                # no building
                if len(os.listdir(run_dir)) <= 4:
                    # shutil.rmtree(run_dir)
                    print(f"Removed directory: {run_dir}")
                    total_removed += 1
    print(f"Total removed directories for failed runs: {total_removed}")


def remove_run_dir(directory: str, n_run: int = 3):

    total_removed = 0
    project_list = os.listdir(directory)
    for project in project_list:
        project_dir = os.path.join(directory, project)
        if not os.path.isdir(project_dir):
            continue
        for func_name in os.listdir(project_dir):
            func_dir = os.path.join(project_dir, func_name)
            for run_name in os.listdir(func_dir):
                run_dir = os.path.join(func_dir, run_name)

                # no building
                if run_name.startswith(f"run{n_run}"):
                    print(f"Removed directory: {run_dir}")

                    shutil.rmtree(run_dir)
                    total_removed += 1

    print(f"Total removed directories for run{n_run}: {total_removed}")


def find_empty_fixes(directory: str):

    project_list = Path(directory)
    for project_dir in project_list.iterdir():
        if not project_dir.is_dir():
            continue
        for func_dir in project_dir.iterdir():
            for run_dir in func_dir.iterdir():
                if not run_dir.is_dir():
                    continue
                for file_path in run_dir.iterdir():
                    if file_path.is_file() and file_path.name.startswith("draft_fix"):
                        text = file_path.read_text()
                        if text.strip() == "":
                            print(f"Empty fix found: {file_path}")
                            shutil.rmtree(run_dir)

def remove_evaluation(eval_path: Path):

    count = 0
    for project in eval_path.iterdir():
        if not project.is_dir():
            continue
        for function_path in project.iterdir():
            if not function_path.is_dir():
                continue
            for work_path in function_path.iterdir():
                if not work_path.is_dir():
                    continue
                if not work_path.name.startswith("run"):
                    print(f"wrong path evaluation dir: {work_path}")
                    continue
                if not (work_path / "fuzzing0.log").exists():
                    shutil.rmtree(work_path)
                    print(f"Removed evaluation dir: {work_path}")
                    count += 1
    print(f"Total removed evaluation dirs: {count}")

def remove_evaluation_pattern(eval_path: Path):

    count = 0
    for project in eval_path.iterdir():
        if not project.is_dir():
            continue
        for function_path in project.iterdir():
            if not function_path.is_dir():
                continue
            for work_path in function_path.iterdir():
                if not work_path.is_dir():
                    continue
                if not work_path.name.startswith("run"):
                    print(f"wrong path evaluation dir: {work_path}")
                    continue
                fuzz_log = work_path / "fuzzing0.log"
                if fuzz_log.exists():
                    try:
              
                        log_text = fuzz_log.read_text(encoding='utf-8', errors='ignore')
                        if 'ERROR: The required directory "/home/yk/code/LLM-reasoning-agents/outputs_evaluation/gpt5-mini/agent' in log_text :
                            shutil.rmtree(work_path)
                            # print(f"Removed evaluation dir with crash: {work_path}")
                            count += 1
                    except Exception as e:
                        print(f"Error reading log file {fuzz_log}: {e}")
                        continue
    print(f"Total removed evaluation dirs: {count}")
    
def get_file_count(directory: Path) -> int:
    count = 0
    for _, _, files in os.walk(directory):
        count += len(files)
    return count

def remove_empty_dir(dir: Path):
    count = 0
    for project in dir.iterdir():
        if not project.is_dir():
            continue
        if get_file_count(project) == 0:
            shutil.rmtree(project)
            count += 1
            print(f"Removed empty dir: {project}")
            continue
        
        for function_path in project.iterdir():
            if not function_path.is_dir():
                continue
            # remove dir without any file inside
            if get_file_count(function_path) == 0:
                shutil.rmtree(function_path)
                count += 1
                print(f"Removed empty dir: {function_path}")
                continue
            
            for work_path in function_path.iterdir():
                if not work_path.is_dir():
                    continue
                if get_file_count(work_path) == 0:
                    shutil.rmtree(work_path)
                    count += 1
                    print(f"Removed empty dir: {work_path}")

    print(f"Total removed empty dirs: {count}")

def filter_eval_projects(save_path: Path, eval_path: Path, threshold: int = 50):

    success_file = save_path / "success_functions_3.json"
    eval_file = eval_path / "evaluation_results.json"
    if not success_file.exists() or not eval_file.exists():
        print("Required files do not exist.")
        return
    
    # load all eval projects
    keep_keys: list[str] = []
    with open(eval_file, 'r') as f:
        eval_data = json.load(f)
        for key, (init_cov, final_cov) in eval_data.items():
            if final_cov - init_cov >= threshold:
                keep_keys.append(key)

    filtered_data: dict[str, Any] = {}
    with open(success_file, 'r') as f:
        success_data = json.load(f)
        for key, value in success_data.items():
            if key in keep_keys:
                filtered_data[key] = value

    print(f"Remain  {len(filtered_data.keys())} functions above threshold {threshold}.")

    with open(save_path / "filtered_success_functions.json", "w") as f:
        json.dump(filtered_data, f, indent=4)

def remove_run_failed_dirs(save_path: Path, n_run: int = 3):

    # read success functions
    success_file = os.path.join(save_path, f"success_functions_{n_run}.json")
    if not os.path.exists(success_file):
        print(f"Success file does not exist: {success_file}")
        return

    # get success function names
    success_funcs: set[str] = set()
    with open(success_file, 'r') as f:
        success_data = json.load(f)
        for _, func_info in success_data.items():
            func_name = Path(func_info["work_dir"]).parent.name
            success_funcs.add(func_name)
    # remove failed function directories

    total_removed = 0
    for project_dir in save_path.iterdir():
        if not project_dir.is_dir():
            continue
        for func_dir in project_dir.iterdir():
            if not func_dir.is_dir():
                continue
            func_name = func_dir.name
            if func_name not in success_funcs:
                shutil.rmtree(func_dir)
                print(f"Removed failed function dir: {func_dir}")
                total_removed += 1

    print(f"Total removed directories for run{n_run} failed runs: {total_removed}")


if __name__ == "__main__":
    # remove_evaluation(Path("/home/yk/code/LLM-reasoning-agents/outputs/projects/evaluation/gpt5-mini/mosquitto"))
    # remove_empty_dir(Path("/home/yk/code/LLM-reasoning-agents/outputs/projects/evaluation/gpt5-mini/mosquitto"))
    # save_path = Path("/home/yk/code/LLM-reasoning-agents/outputs/projects/gpt5-mini/libxml2/")
    # eval_path = Path("/home/yk/code/LLM-reasoning-agents/outputs/projects/evaluation/gpt5-mini/libxml2/")

    # filter_eval_projects(save_path, eval_path, threshold=10)

    # remove_failed_dir("/home/yk/code/LLM-reasoning-agents/outputs/projects/gpt5-mini/libxml2/")
    # remove_empty_dir(Path("/home/yk/code/LLM-reasoning-agents/outputs/projects/gpt5-mini/net-snmp"))
    # remove_empty_cache("/home/yk/code/LLM-reasoning-agents/cache")
    # remove_evaluation(Path("/home/yk/code/LLM-reasoning-agents/outputs/projects/evaluation/gpt5-mini/libxml2"))
    # Example usage
    # remove_corpus_dir("/home/yk/code/LLM-reasoning-agents/outputs_wild")
    # remove_large_log_files("/home/yk/code/LLM-reasoning-agents/outputs_wild")
    remove_run_dir("/home/yk/code/LLM-reasoning-agents/outputs/projects/gpt5-mini/openldap", n_run=2)
    # filter_eval_projects(Path("/home/yk/code/LLM-reasoning-agents/outputs/projects/gpt5-mini/libxml2/"),
                        #  Path("/home/yk/code/LLM-reasoning-agents/outputs/projects/evaluation/gpt5-mini/libxml2/"),
                        #  threshold=10)
