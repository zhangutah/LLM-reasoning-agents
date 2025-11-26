import json
import os
from math import ceil
from agent_tools.fuzz_tools.run_fuzzer import FuzzerRunner
from agent_tools.fuzz_tools.compiler import Compiler
from harness_agent.modules.fuzzenv import FuzzENV
from bench_cfg import BenchConfig
from pathlib import Path
from constants import CompileResults, ValResult, PROJECT_PATH
from utils.misc import extract_name
from agent_tools.fuzz_tools.cov_collecter import CovCollector
from typing import Optional
import multiprocessing
import psutil
from utils.misc import logger_wrapper
import shutil

class HarnessEval(FuzzENV):
    def __init__(self,  benchcfg: BenchConfig, function_signature: str, project_name: str, local_harness: Path, n_run: int=1):
        super().__init__(benchcfg=benchcfg, function_signature=function_signature, project_name=project_name, n_run=n_run)
        self.harness_code = local_harness.read_text()

    def eval_harness(self) -> tuple[int, int, bool]:

        compiler = Compiler(self.benchcfg.oss_fuzz_dir, self.benchcfg.benchmark_dir, self.project_name, self.new_project_name)

        fuzzer = FuzzerRunner(oss_fuzz_dir=self.benchcfg.oss_fuzz_dir, new_project_name=self.new_project_name,
                project_lang=self.project_lang, run_timeout=self.benchcfg.run_time, save_dir=self.save_dir)
        
        compile_res = CompileResults.FuzzerError
        for fuzzer_name, harness_path in self.harness_pairs.items():
          
            compile_res, _ = compiler.compile_harness(harness_code=self.harness_code, harness_path=harness_path, fuzzer_name=fuzzer_name)
            if compile_res != CompileResults.Success:
                continue

            # Run the fuzzer
            fuzz_res, _, _ = fuzzer.run_fuzzing(counter=0, fuzzer_name=fuzzer_name, ignore_crashes=self.benchcfg.ignore_crashes, no_log=self.benchcfg.no_log)
            if fuzz_res != ValResult.NoError:
                logger_wrapper(self.logger, f"Crash when fuzzing: {fuzz_res}", level="error")
                # return 0,0, False
                # copy the crash file to save_dir
                out_path = self.benchcfg.oss_fuzz_dir / "build" / "out" /self.new_project_name
                crash_files = list(out_path.glob("crash-*"))
                for crash_file in crash_files:
                    dest_file = self.save_dir / crash_file.name
                    os.makedirs(self.save_dir, exist_ok=True)
                    shutil.copy(crash_file, dest_file)

                
            logger_wrapper(self.logger, f"Collecting coverage for {fuzzer_name}", level="info")
            corpus_dir = Path(self.save_dir) / "corpora"
            function_name =  extract_name(self.function_signature, keep_namespace=True, exception_flag=False, language=self.project_lang)
            # init the cov collector
            cov_collector = CovCollector(self.benchcfg.oss_fuzz_dir, self.benchcfg.benchmark_dir, self.project_name, self.new_project_name, self.project_lang, self.logger)
            # collect the coverage
            init_cov, final_cov, changed = cov_collector.collect_coverage(self.harness_code, harness_path, fuzzer_name, function_name, corpus_dir)
            
            return init_cov, final_cov, changed

        logger_wrapper(self.logger, f"All fuzzer compilation failed: {compile_res}", level="error")
        # Run the harness with the specified path
        return 0, 0, False
    

def process_single_result(args: tuple[str, str, Path, BenchConfig]): # type: ignore
    """Process a single project/function/workdir combination"""
    project_name, function_signature, work_dir, benchcfg = args # type: ignore
    harness_file = work_dir / "harness.txt"
    
    try:
        # get the evaluator
        evaluator = HarnessEval(benchcfg=benchcfg, function_signature=function_signature, project_name=project_name, local_harness=harness_file)
        if evaluator.early_exit_flag:
            return project_name, function_signature, 0, 0
        init_cov, final_cov, _ = evaluator.eval_harness()

        # save coverage
        cov_file = evaluator.save_dir / "cov.txt"
        with open(cov_file, "w") as f:
            f.write(f"Initial coverage: {init_cov}\n")  
            f.write(f"Final coverage: {final_cov}\n")

        return project_name, function_signature, init_cov, final_cov
    except Exception as e:
        print(f"Error processing {project_name}/{function_signature}: {e}")
        return project_name, function_signature, 0, 0

def run_evaluation(output_path: Path, benchcfg:BenchConfig, n_run:int=1, num_processes:Optional[int]=None, n_partitations:int=1, partitation_id:int=0): 
    if num_processes is None:
        num_processes = max(1, psutil.cpu_count(logical=False))  # type: ignore

    res_json = output_path / f"success_functions_{n_run}.json"
    if not res_json.exists():
        print(f"No success_functions_{n_run}.json found in {output_path.parent}, exit.")
        return
    
    with open(res_json, "r") as f:
        res_data = json.load(f)

    # Prepare arguments for multiprocessing
    args_list: list[tuple[str, str, Path, BenchConfig]] = []
    for key, value in list(res_data.items()):
        _, func_sig = key.split("+")
        args_list.append((value.get("project"), func_sig, Path(value.get("work_dir")), benchcfg))

    # Partition the args_list if needed
    if n_partitations > 1:
        total_len = len(args_list)
        part_size = ceil(total_len / n_partitations)  # Ceiling division
        start_index = partitation_id * part_size
        end_index = min(start_index + part_size, total_len)
        args_list = args_list[start_index:end_index]
        print(f"Processing partition {partitation_id + 1}/{n_partitations}, items {start_index} to {end_index - 1}")

    # Run evaluation in parallel
    with multiprocessing.Pool(processes=num_processes) as pool:
        results = pool.map(process_single_result, args_list) # type: ignore
    
    # Process results
    os.makedirs(benchcfg.save_root, exist_ok=True)
    # res_file = benchcfg.save_root / f"evaluation_{n_run}_{partitation_id}.txt"
    
    # with open(res_file, "w") as save_f:
        # for project_name, function_signature, init_cov, final_cov in results: # type: ignore
            # save_f.write(f"{project_name}/{extract_name(function_signature, keep_namespace=True, exception_flag=False)}. Initial coverage: {init_cov}, Final coverage: {final_cov}\n")
# 

if __name__ == "__main__":

    
    # using args
    from argparse import ArgumentParser

    parser  = ArgumentParser(description="Run harness evaluation in parallel.")
    parser.add_argument("--output_path", type=str, default=f"{PROJECT_PATH}/outputs/wild/gpt5-mini/raw", help="Path to the output directory containing success_functions.json")
    parser.add_argument("--benchcfg_path", type=str, default=f"{PROJECT_PATH}/cfg/eval_cfg.yaml", help="Path to the benchmark configuration YAML file")
    parser.add_argument("--n_run", type=int, default=3, help="Run number corresponding to success_functions_{n_run}.json")
    parser.add_argument("--num_processes", type=int, default=None, help="Number of parallel processes to use. Defaults to number of CPU cores minus one.")
    parser.add_argument("--n_partitations", type=int, default=1, help="Total number of partitions to divide the workload into.")
    parser.add_argument("--partitation_id", type=int, default=0, help="ID of the partition to process (0-indexed).")
    args = parser.parse_args()

    benchcfg = BenchConfig(args.benchcfg_path)
    run_evaluation(output_path=Path(args.output_path), benchcfg=benchcfg, n_run=args.n_run,
                    num_processes=args.num_processes, n_partitations=args.n_partitations, partitation_id=args.partitation_id)