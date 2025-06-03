import os
import pickle
import signal
import sys
import yaml
from multiprocessing import Pool
from utils.misc import extract_name, get_benchmark_functions
from issta.issta import ISSTAFuzzer
from tools.results_analysis import run_agent_res
from constants import PROJECT_PATH
from pathlib import Path
import traceback  # Add this at the top

class Runner:
    def __init__(self, config_path: str):
        """Initialize the Runner with configuration from a YAML file.
        Args:
            config_path (str): Path to the YAML configuration file
        """
        self.config = self._load_config(config_path)
        
        # Initialize all parameters as class members with defaults from config
        self.oss_fuzz_dir = Path(self.config.get('oss_fuzz_dir', '/home/yk/code/oss-fuzz/'))
        self.cache_dir = Path(self.config.get('cache_dir', "/home/yk/code/LLM-reasoning-agents/cache/"))
        self.bench_dir = Path( self.config.get('bench_dir', os.path.join(PROJECT_PATH, "benchmark-sets", "ntu")))
        self.save_dir = Path(self.config.get('save_dir', os.path.join(PROJECT_PATH, "outputs", "issta_rank_one")))

        # absolute path
        if not self.save_dir.is_absolute():
            self.save_dir = PROJECT_PATH /  self.save_dir

        self.model_name = self.config.get('model_name', "gpt-4-0613")
        self.temperature = self.config.get('temperature', 0.7)
        self.run_time = self.config.get('run_time', 1)
        self.max_fix = self.config.get('max_fix', 5)
        self.max_tool_call = self.config.get('max_tool_call', 15)
        self.usage_token_limit = self.config.get('usage_token_limit', 1000)
        self.model_token_limit = self.config.get('model_token_limit', 8096)
        self.n_examples = self.config.get('n_examples', 1)
        self.example_mode = self.config.get('example_mode', "rank")
        self.iterations = self.config.get('iterations', 3)
        self.num_processes = self.config.get('num_processes', os.cpu_count() // 2) # type: ignore
        self.project_name = self.config.get('project_name')
        self.function_signatures = self.config.get('function_signatures', [])
        self.clear_msg_flag = self.config.get('clear_msg_flag', True)
        self.tool_flag = self.config.get('tool_flag', False)
        
    def _load_config(self, config_path: str):
        """Load configuration from a YAML file."""
        with open(config_path, 'r') as f:
            return yaml.safe_load(f)
    
    def get_successful_func(self) -> list[str]:
    
        all_success_sig: list[str] = []
        for i in range(1, self.iterations):

            res_file = os.path.join(self.save_dir, f"success_name_{i}.pkl")
            if not os.path.exists(res_file):
                continue
                
            with open(res_file, "rb") as f:
                success_sig = pickle.load(f)
                all_success_sig.extend(success_sig)

        return all_success_sig


    def filter_functions(self, function_dict: dict[str, list[str]], success_func: list[str]) -> dict[str, list[str]]:
        """Filter out functions that are already successful."""
        for key in function_dict.keys():
            function_list = function_dict[key]
            # filter out the functions that are already successful
            new_function_list: list[str] = []
            for func_sig in function_list:
                function_name = extract_name(func_sig)
                if (function_name not in success_func) and (function_name.lower() not in success_func):
                    new_function_list.append(func_sig)

            function_dict[key] = new_function_list
        return function_dict
    
    def get_num_function(self, function_dict: dict[str, list[str]]) -> tuple[int, int]:
        """Get the maximum number of functions across all projects."""
        total = 0
        max_num_function = 0
        for key in function_dict.keys():
            total += len(function_dict[key])
            if len(function_dict[key]) > max_num_function:
                max_num_function = len(function_dict[key])
        return max_num_function, total


    @staticmethod
    def run_one(n_examples: int, example_mode: str, model_name: str, temperature: float,  oss_fuzz_dir: Path, 
                 project_name: str, function_signature: str, usage_token_limit: int, 
                 model_token_limit: int, run_time: int, max_fix: int, max_tool_call: int,
                 clear_msg_flag:bool, save_dir: Path, cache_dir: Path, n_run: int = 1, tool_flag:bool=False):
        """Run the fuzzer on a single function."""

        agent_fuzzer = ISSTAFuzzer(n_examples, example_mode, model_name, temperature, oss_fuzz_dir,
                                        project_name, function_signature, usage_token_limit, 
                                        model_token_limit, run_time, max_fix,
                                        max_tool_call, clear_msg_flag, save_dir, 
                                        cache_dir, n_run, tool_flag=tool_flag)
        try:
        # Your main logic here
            graph = agent_fuzzer.build_graph()
            agent_fuzzer.run_graph(graph)

        except Exception as e:
            agent_fuzzer.logger.error(f"Exit. An exception occurred: {e}")
            traceback.print_exc() 
        finally:
            agent_fuzzer.clean_workspace()
    

    def run_all(self, max_num_function: int, function_dict: dict[str, list[str]], n_run: int=1):
        """Run the fuzzer on all functions in parallel."""

        with Pool(processes=self.num_processes) as pool:
            for i in range(max_num_function):
                for key in function_dict.keys():
                    if i >= len(function_dict[key]):
                        continue
                    function_signature = function_dict[key][i]
                    project_name = key
                    print(f"{i+1}th of functions in {key}: {len(function_dict[key])}")
                    
                    pool.apply_async(Runner.run_one, args=(
                        self.n_examples, self.example_mode, self.model_name, self.temperature, self.oss_fuzz_dir, 
                        project_name, function_signature, self.usage_token_limit, 
                        self.model_token_limit, self.run_time, self.max_fix, self.max_tool_call, self.clear_msg_flag,
                        self.save_dir, self.cache_dir, n_run, self.tool_flag))
            pool.close()
            pool.join()

    def run(self):
        """Run parallel execution with configuration from YAML file.
        
        Args:
            iterations (int, optional): Number of iterations, uses config value if None
        """
        # copy the config file to the save directory
        if not os.path.exists(self.save_dir):
            os.makedirs(self.save_dir)
        config_file = os.path.join(self.save_dir, "config.yaml")
        with open(config_file, 'w') as f:
            yaml.dump(self.config, f)
        function_dicts = get_benchmark_functions(self.bench_dir,
                                                 allowed_projects=[self.project_name] if self.project_name else [],
                                                 allowed_langs=["c", "c++"],
                                                 allowed_functions=self.function_signatures)

       
        for i in range(self.iterations):
            iter_res = self.save_dir / "res_{}.txt".format(i+1)
            if iter_res.exists():
                print(f"Iteration {i+1} already completed. Skipping...")
                continue
            print(f"Running iteration {i+1} of {self.iterations}...")
            success_func = self.get_successful_func()
            todo_function_dicts = self.filter_functions(function_dicts, success_func)
            max_num_function, total_function = self.get_num_function(todo_function_dicts)

            if total_function == 0:
                print("All functions are successful. Exiting...")
                break
            print(f"Iteration {i+1} of {self.iterations}: {total_function} functions to run")

            self.run_all(max_num_function, todo_function_dicts, n_run=i+1)

            run_agent_res(self.save_dir, method="issta", n_run=i+1)

    def run_single(self):
        """Run single execution with configuration from YAML file."""

        assert len(self.function_signatures) > 0, "No function signatures provided in the config file."
        assert self.project_name is not None, "No project name provided in the config file."

        iterations_list = self.config.get('iterations_list', [1])
        
        for function_signature in self.function_signatures:
            for i in iterations_list:
  
               
                Runner.run_one(
                    self.n_examples, self.example_mode, self.model_name, self.temperature, self.oss_fuzz_dir, 
                    self.project_name, function_signature, self.usage_token_limit, 
                    self.model_token_limit, self.run_time, self.max_fix, self.max_tool_call, 
                    self.clear_msg_flag, self.save_dir, self.cache_dir,  n_run=i+1,  tool_flag=self.tool_flag
                )


if __name__ == "__main__":
    # # Check if the script is being run directly
    # if len(sys.argv) < 2:
    #     print("Usage: python run.py <config_path>")
    #     sys.exit(1)

    config_path = "/home/yk/code/LLM-reasoning-agents/cfg/ntu_41.yaml"
    runner = Runner(config_path)
    
    # Set up signal handling for graceful termination
    def signal_handler(sig, frame): # type: ignore
        print('Exiting gracefully...')
        sys.exit(0)

    signal.signal(signal.SIGINT, signal_handler) # type: ignore
    
    # Run the main function
    runner.run()