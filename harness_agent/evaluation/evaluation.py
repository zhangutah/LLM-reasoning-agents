import os
import shutil
import re
from agent_tools.fuzz_tools.run_fuzzer import FuzzerRunner
from agent_tools.fuzz_tools.compiler import Compiler
from harness_agent.modules.fuzzenv import FuzzENV
from bench_cfg import BenchConfig
from pathlib import Path
from constants import CompileResults, EvalResult, PROJECT_PATH, LanguageType, FuzzResult
from utils.misc import extract_name
from agent_tools.fuzz_tools.cov_collecter import CovCollector
import pickle
from typing import DefaultDict, Optional
from collections import defaultdict
from agent_tools.code_tools.parsers.cpp_parser import CPPParser
import multiprocessing
# from functools import partial
from utils.misc import logger_wrapper

def parse_target_cmd(build_msg: str, fuzzer_name: str) -> str:
    for line in build_msg.splitlines():
        if re.search(r"-o\s+(?:[\S/]*/)?" + re.escape(fuzzer_name) + r"(?:\s+|$)", line) and "clang" in line:
            # This function extracts the target command for the given fuzzer
            return line
    return ""

class HarnessEval(FuzzENV):
    def __init__(self,  benchcfg: BenchConfig, function_signature: str, project_name: str, local_harness: str, n_run: int=1):
        super().__init__(benchcfg=benchcfg, function_signature=function_signature, project_name=project_name, n_run=n_run)
        self.harness_code = Path(local_harness).read_text()

    def eval_harness(self) -> tuple[int, int, bool]:

        compiler = Compiler(self.benchcfg.oss_fuzz_dir, self.benchcfg.benchmark_dir, self.project_name, self.new_project_name)

        fuzzer = FuzzerRunner(oss_fuzz_dir=self.benchcfg.oss_fuzz_dir, new_project_name=self.new_project_name,
                project_lang=self.project_lang, run_timeout=self.benchcfg.run_time, save_dir=self.save_dir)
        
        compile_res = CompileResults.FuzzerError
        for fuzzer_name, harness_path in self.harness_pairs.items():
            compile_res, build_msg = compiler.compile(harness_code=self.harness_code, harness_path=harness_path, fuzzer_name=fuzzer_name)
            if compile_res != CompileResults.Success:
                continue

            # extract target compile cmd
            target_cmd = parse_target_cmd(build_msg, fuzzer_name)
            if not target_cmd:
                logger_wrapper(self.logger, "Empty Target cmd", level="error")
                return 0,0, False
            
            if target_cmd.startswith("+"):
                target_cmd = target_cmd[1:].strip()
         
            # add open wrap $CC $CFLAGS -c openwrapper.c -o /src/openwrapper.o
            # mv /usr/local/bin/clang /usr/local/bin/clang.orig
          
            target_cmd = target_cmd.replace("{}.o".format(fuzzer_name),"{}.o /src/openwrapper.o".format(fuzzer_name))
            cmd = "$CC $CFLAGS -c /src/openwrapper.c -o /src/openwrapper.o && {} -Wl,--wrap=open -Wl,--wrap=fopen".format(target_cmd)

            # write this cmd to build.sh

            build_sh_path = os.path.join(self.benchcfg.oss_fuzz_dir, "projects", self.new_project_name, "build.sh")
            shutil.copy(build_sh_path, build_sh_path + ".bak")
            with open(build_sh_path, "a") as f:
                f.write(cmd + "\n")
            new_project_dir = os.path.join(self.benchcfg.oss_fuzz_dir,"projects", self.new_project_name)
            
            # copy the openwrap.c to out dir
            shutil.copy(f"{PROJECT_PATH}/harness_agent/evaluation/openwrap.c", new_project_dir)
            copy_cmd = f"\nCOPY openwrap.c /src/openwrap.c\n"
            compile_res, build_msg = compiler.compile(harness_code=self.harness_code, harness_path=harness_path, fuzzer_name=fuzzer_name, cmd=copy_cmd)
            
            # restore the build.sh
            shutil.copy(build_sh_path + ".bak", build_sh_path)
            compiler.write_dockerfile(harness_path, write_flag=False)
            
            if compile_res != CompileResults.Success:
                # Propagate the error from run_cmd
                logger_wrapper(self.logger, f"Compile Error during linking with open wrap: {build_msg}", level="error")
                return 0,0, False

            # Run the fuzzer
            fuzz_res, _, _ = fuzzer.run_fuzzing(counter=0, fuzzer_name=fuzzer_name)
            if fuzz_res != FuzzResult.NoError:
                logger_wrapper(self.logger, f"Error during fuzzing: {fuzz_res}", level="error")
                return 0,0, False
            

            corpus_dir = Path(self.save_dir) / "corpora"
            function_name =  extract_name(self.function_signature, keep_namespace=True)
            # init the cov collector
            cov_collector = CovCollector(self.benchcfg.oss_fuzz_dir, self.benchcfg.benchmark_dir, self.project_name, self.new_project_name, self.project_lang, self.logger)
            # collect the coverage
            init_cov, final_cov, chenged = cov_collector.collect_coverage(self.harness_code, harness_path, fuzzer_name, function_name, corpus_dir)
            
            return init_cov, final_cov, chenged

        logger_wrapper(self.logger, f"All fuzzer compilation failed: {compile_res}", level="error")
        # Run the harness with the specified path
        return 0, 0, False
    

def get_harness(work_path: Path) -> str:
    log_lines = (work_path / "agent.log").read_text()
    pattern = r".*Run (\d+)th Fuzzer.*"

    line_list = log_lines.split("\n")
    for i, line in enumerate(line_list):
        match = re.match(pattern, line)
        if match and "NoError" in line_list[i+1]:
            return str(work_path / "draft_fix{}.txt".format(match.group(1)))

    return ""

def get_automatic_res(work_dir: Path, project_name: str, benchcfg: BenchConfig):

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
    
    harness = get_harness(work_dir)
    if harness == "":
        print(f"No valid harness found for {project_name}/{function_name} in {work_dir}")
        return EvalResult.Failed, usage_flag

    # get the evaluator
    evaluator = HarnessEval(benchcfg=benchcfg, function_signature=function_signature, project_name=project_name, local_harness=harness)
    init_cov, final_cov, _ = evaluator.eval_harness()

    # save coverage
    cov_file = evaluator.save_dir / "cov.txt"
    with open(cov_file, "w") as f:
        f.write(f"Initial coverage: {init_cov}\n")  
        f.write(f"Final coverage: {final_cov}\n")

    # not reach here
    if init_cov == 0:
        return EvalResult.Failed, usage_flag

    parser = CPPParser(file_path=harness_path, project_lang=LanguageType.CPP)

    if parser.exist_function_definition(function_name):
        return EvalResult.Fake, usage_flag
    
    if parser.is_fuzz_function_called(function_name):
        return EvalResult.Success, usage_flag
    else:
        return EvalResult.NoCall, usage_flag
    

def process_single_result(args): # type: ignore
    """Process a single project/function/workdir combination"""
    project_name, function_name, work_dir, benchcfg = args # type: ignore
    eval_res, usage_flag = get_automatic_res(work_dir, project_name, benchcfg) # type: ignore
    return project_name, function_name, eval_res, usage_flag # type: ignore

def run_agent_res(output_path: Path, benchcfg:BenchConfig,  n_run:int=1, num_processes:Optional[int]=None): 
    if num_processes is None:
        num_processes = max(1, multiprocessing.cpu_count() - 1)  # Use all but one CPU core

    res_count: DefaultDict[str, int] = defaultdict(int)
    output_path = Path(output_path)
    success_name_list:list[str] = []
    
    # save the projects whose functions are all failed
    build_failed_functions: list[str] = []
    failed_projects: set[str] = set()
    all_path:list[tuple[str, str, Path]] = []
    
    for project_path in output_path.iterdir():
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
                # check if the directory is empty
                if len(os.listdir(work_dir)) <= 4:
                    build_failed_functions.append(f"{project_path.name}/{function_path.name}")
                    continue

                # get the run number
                n = int(work_dir.name.split("_")[0][3:])
                if n <= n_run:
                    all_path.append((project_path.name, function_path.name, work_dir))
                    failed_projects.add(project_path.name)
    
    # Prepare arguments for multiprocessing
    args_list = [(project_name, function_name, work_dir, benchcfg) 
                for project_name, function_name, work_dir in all_path]
    
    # Run evaluation in parallel
    print(f"Processing {len(args_list)} items using {num_processes} processes...")
    with multiprocessing.Pool(processes=num_processes) as pool:
        results = pool.map(process_single_result, args_list) # type: ignore
    
    # Process results
    os.makedirs(benchcfg.save_root, exist_ok=True)
    res_file = benchcfg.save_root / f"res_{n_run}.txt"
    
    with open(res_file, "w") as save_f:
        for project_name, function_name, eval_res, usage_flag in results: # type: ignore
            res_count[eval_res.value] += 1

            if eval_res != EvalResult.Success:
                continue
            if project_name in failed_projects:
                failed_projects.remove(project_name) # type: ignore
            
            #  only count once
            if function_name in success_name_list:
                continue
            success_name_list.append(function_name) # type: ignore
            if usage_flag:
                save_f.write(f"{project_name}/{function_name}. fuzz res: {eval_res}, HaveUsage\n")
            else:
                save_f.write(f"{project_name}/{function_name}. fuzz res: {eval_res}, NoUsage\n")
        
        save_f.write(f"Results count: {res_count}\n")
        save_f.write(f"Success:{res_count['Success'] }")

    with open(os.path.join(output_path, f"failed_projects_{n_run}.txt"), "w") as f:
        for project in failed_projects:
            f.write(f"{project}\n")

    with open(os.path.join(output_path, f"build_failed_functions_{n_run}.txt"), "w") as f:
        for func in build_failed_functions:
            f.write(f"{func}\n")

    pickle.dump(success_name_list, open(os.path.join(output_path, f"success_name_{n_run}.pkl"), "wb"))



if __name__ == "__main__":

    benchcfg_path = "/home/yk/code/LLM-reasoning-agents/cfg/eval_cfg.yaml"
    benchcfg = BenchConfig(benchcfg_path)
    output_path = "/home/yk/code/LLM-reasoning-agents/outputs_ablation/claude_sonnet/code_info/agent"
    n_run = 1
    # run_agent_res(Path(output_path), benchcfg,  n_run, num_processes=8)

    function_signature = "bool stun_is_command_message(const stun_buffer *)"
    project = "coturn"
    harness = "/home/yk/code/LLM-reasoning-agents/outputs_ablation/claude_sonnet/code_info/agent/coturn/stun_is_command_message/run1_gjneppfhcifvlxwi/draft_fix0.txt"
    evaluator = HarnessEval(benchcfg=benchcfg, function_signature=function_signature, project_name=project, local_harness=harness)
    res = evaluator.eval_harness()
    if res[-1]:
        print(res)
    else:
        print("Harness evaluation failed.")