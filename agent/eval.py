import json
import os
from math import ceil
from agent_tools.fuzz_tools.run_fuzzer import FuzzerRunner
from agent_tools.fuzz_tools.compiler import Compiler
from agent.modules.fuzzenv import FuzzENV
from bench_cfg import BenchConfig
from pathlib import Path
from constants import CompileResults, ValResult, PROJECT_PATH
from utils.misc import extract_name
from agent_tools.fuzz_tools.cov_collecter import CovCollector
import multiprocessing
import psutil
import shutil
from utils.misc import extract_fuzzer_name

class HarnessEval(FuzzENV):
    def __init__(self,  benchcfg: BenchConfig, function_signature: str, project_name: str, local_harness: Path, n_run: int=1):
        super().__init__(benchcfg=benchcfg, function_signature=function_signature, project_name=project_name, n_run=n_run,
                          eval_flag=True)
        self.harness_code = local_harness.read_text()

    def eval_harness(self, fuzzer_name: str, harness_path: Path, include_path_set: set[str] = set()) -> tuple[int, int, bool]:
        '''
        :param fuzzer_name: the fuzzer name associated with the harness
        :param harness_path: the path of the harness file inside the oss-fuzz project docker image
        '''
        compiler = Compiler(self.benchcfg.oss_fuzz_dir, self.benchcfg.benchmark_dir, self.project_name,
                             self.new_project_name, include_path_set, self.code_retriever, self.function_signature)

        fuzzer = FuzzerRunner(oss_fuzz_dir=self.benchcfg.oss_fuzz_dir, new_project_name=self.new_project_name,
                project_lang=self.project_lang, run_timeout=self.benchcfg.run_time, save_dir=self.save_dir)
        
        compile_res, _ = compiler.compile_harness(harness_code=self.harness_code, harness_path=harness_path, fuzzer_name=fuzzer_name)
        if compile_res != CompileResults.Success:
            self.logger.error(f"Fuzzer compilation failed: {compile_res}") if self.logger else None
            return 0, 0, False

        self.logger.info(f"Start Fuzzing with {fuzzer_name} for harness at {harness_path}") if self.logger else None
        # Run the fuzzer
        fuzz_res, _, _ = fuzzer.run_fuzzing(counter=0, fuzzer_name=fuzzer_name, 
                                            ignore_crashes=self.benchcfg.ignore_crashes, no_log=self.benchcfg.no_log)
        if fuzz_res != ValResult.NoError:
            self.logger.error(f"Crash when fuzzing: {fuzz_res}") if self.logger else None
            # return 0,0, False
            # copy the crash file, leak, timeout to save_dir
            out_path = self.benchcfg.oss_fuzz_dir / "build" / "out" /self.new_project_name

            for pattern in ["crash-*", "leak-*", "timeout-*", "oom-*"]:
                crash_files = list(out_path.glob(pattern))
                for crash_file in crash_files:
                    dest_file = self.save_dir / crash_file.name
                    os.makedirs(self.save_dir, exist_ok=True)
                    shutil.copy(crash_file, dest_file)

            
        self.logger.info(f"Fuzzing over. Collecting coverage for {fuzzer_name}") if self.logger else None
        corpus_dir = Path(self.save_dir) / "corpora"
        function_name = extract_name(self.function_signature, keep_namespace=True, exception_flag=False, language=self.project_lang)
        # init the cov collector
        cov_collector = CovCollector(self.benchcfg.oss_fuzz_dir, self.benchcfg.benchmark_dir, self.project_name,
                                      self.new_project_name, self.project_lang, self.logger)
        # collect the coverage
        init_cov, final_cov, changed = cov_collector.collect_coverage(self.harness_code, harness_path, fuzzer_name, function_name, corpus_dir)
        
        return init_cov, final_cov, changed



def process_single_result(args: tuple[str, str, Path, BenchConfig]): # type: ignore
    """Process a single project/function/workdir combination"""
    project_name, function_signature, work_dir, benchcfg = args # type: ignore
    local_harness_file = work_dir / "harness.txt"
    
    fuzzer_info_file = work_dir / "fuzzer_info.json"
    if not fuzzer_info_file.exists():
        # read the agent log
        agent_log_file = work_dir / "agent.log"
        log_lines = agent_log_file.read_text().splitlines()
        # read the fuzzer name and harness path
        fuzzer_name, remote_harness_path = extract_fuzzer_name(log_lines)
    else:
        with open(fuzzer_info_file, "r") as f:
            fuzzer_info = json.load(f)
        fuzzer_name = fuzzer_info["fuzzer_name"]
        remote_harness_path = fuzzer_info["fuzzer_path"]

    if fuzzer_name == "" or remote_harness_path == "":
        print(f"Fuzzer info incomplete in file: {fuzzer_info_file}")
        return project_name, function_signature, 0, 0

    include_file = work_dir / "include_path.txt"
    include_path_set: set[str] = set()
    if include_file.exists():
        for line in include_file.read_text().splitlines():
            include_path_set.add(line.strip())

    # print("harness_path:", remote_harness_path)
    remote_harness_path = Path(remote_harness_path)

    try:
        # get the evaluator
        evaluator = HarnessEval(benchcfg=benchcfg, function_signature=function_signature,
                                project_name=project_name, local_harness=local_harness_file, n_run=1)
        if evaluator.early_exit_flag:
            return project_name, function_signature, 0, 0
        init_cov, final_cov, _ = evaluator.eval_harness(fuzzer_name=fuzzer_name, harness_path=remote_harness_path, include_path_set=include_path_set)

        # copy harness file to local project dir
        eval_harness_dir = evaluator.save_dir / "harness.txt"
        shutil.copy(local_harness_file, eval_harness_dir)

        # save coverage
        cov_file = evaluator.save_dir / "cov.txt"
        with open(cov_file, "w") as f:
            f.write(f"Initial coverage: {init_cov}\n")  
            f.write(f"Final coverage: {final_cov}\n")

        return project_name, function_signature, init_cov, final_cov
    except Exception as e:
        print(f"Error processing {project_name}/{function_signature}: {e}")
        return project_name, function_signature, 0, 0

def run_evaluation(output_path: Path, benchcfg:BenchConfig, n_run:int=1, n_partitations:int=1, partitation_id:int=0): 
    """Run the evaluation in parallel"""

    # too many processes will cause docker build image and build fuzzer failed
    total_core = psutil.cpu_count(logical=False)  
    num_processes = (total_core // 3 *2)  # type: ignore
    # if benchcfg.num_processes is not None:
        # num_processes = min(benchcfg.num_processes, num_processes) # type: ignore
    num_processes = benchcfg.num_processes if benchcfg.num_processes is not None else num_processes # type: ignore
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
    with multiprocessing.Pool(processes=num_processes) as pool: # type: ignore
        results = pool.map(process_single_result, args_list) # type: ignore

if __name__ == "__main__":

    
    # using args
    from argparse import ArgumentParser

    parser  = ArgumentParser(description="Run harness evaluation in parallel.")
    parser.add_argument("--output_path", type=str, default=f"{PROJECT_PATH}/outputs/projects/gpt5-mini/nginx/", help="Path to the output directory containing success_functions.json")
    parser.add_argument("--benchcfg_path", type=str, default=f"{PROJECT_PATH}/cfg/gpt5_mini/projects/nginx_eval.yaml", help="Path to the benchmark configuration YAML file")
    parser.add_argument("--n_run", type=int, default=3, help="Run number corresponding to success_functions_{n_run}.json")
    parser.add_argument("--n_partitations", type=int, default=1, help="Total number of partitions to divide the workload into.")
    parser.add_argument("--partitation_id", type=int, default=0, help="ID of the partition to process (0-indexed).")
    args = parser.parse_args()

    benchcfg = BenchConfig(args.benchcfg_path)
    run_evaluation(output_path=Path(args.output_path), benchcfg=benchcfg, n_run=args.n_run,
                    n_partitations=args.n_partitations, partitation_id=args.partitation_id)