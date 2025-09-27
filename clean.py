import os
from pathlib import Path    
import shutil

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
                    shutil.rmtree(run_dir)
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
                            
# Example usage
remove_corpus_dir("/home/yk/code/LLM-reasoning-agents/outputs_wild")
# remove_large_log_files("/home/yk/code/LLM-reasoning-agents/outputs_wild/gpt5-mini/raw")
# remove_run_dir("/home/yk/code/LLM-reasoning-agents/outputs_ablation/claude_sonnet/code_info/oss_fuzz", n_run=3)