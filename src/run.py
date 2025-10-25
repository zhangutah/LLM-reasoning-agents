import os
import signal
import sys
import time
import yaml
from multiprocessing import Pool
from utils.misc import extract_name, get_benchmark_functions
from harness_agent.main import ISSTAFuzzer
from agent_tools.results_analysis import run_agent_res
from bench_cfg import BenchConfig
import traceback  # Add this at the top
import json

class Runner:
    def __init__(self, cfg_path: str):
        """Initialize the Runner with configuration from a YAML file.
        Args:
            cfg_path (str): Path to the YAML configuration file
        """
        self.config = BenchConfig(cfg_path)
        self.cfg_path = cfg_path
        
    def get_successful_func(self) -> list[str]:
    
        all_success_sig: list[str] = []
        for i in range(1, self.config.iterations):

            res_file = os.path.join(self.config.save_root, f"success_functions_{i}.json")
            if not os.path.exists(res_file):
                continue

            with open(res_file, "r") as f:
                success_data = json.load(f)
                all_success_sig.extend(success_data.keys())

        return all_success_sig


    def filter_functions(self, function_dict: dict[str, list[str]], success_func: list[str]) -> dict[str, list[str]]:
        """Filter out functions that are already successful."""
        for key in function_dict.keys():
            function_list = function_dict[key]
            # filter out the functions that are already successful
            new_function_list: list[str] = []
           
            for func_sig in function_list:
                if func_sig in success_func:
                    continue
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
    def run_one(config: BenchConfig, function_signature: str, project_name: str, n_run: int=1):
        """Run the fuzzer on a single function."""

        agent_fuzzer = ISSTAFuzzer(config, function_signature, project_name, n_run=n_run)
        try:
        # Your main logic here
            graph = agent_fuzzer.build_graph()
            agent_fuzzer.run_graph(graph)

        except Exception as e:
            agent_fuzzer.logger.error(f"Exit. An exception occurred: {e}")
            traceback.print_exc() 
        finally:
            agent_fuzzer.clean_workspace()
    

    def has_run(self, function_signature: str, project_name: str, n_run: int) -> bool:
        function_name = extract_name(function_signature, keep_namespace=True)
        function_name = function_name.replace("::", "_")  # replace namespace with underscore
        save_dir = self.config.save_root / project_name.lower() / function_name.lower() 
        
        if not save_dir.exists():
            return False
        
        run_flag = False
        for path in save_dir.iterdir():  # ensure the directory exists
            if path.name.startswith(f"run{n_run}"):
                # print(f"Skipping {function_name} in {project_name} for run {n_run}, already exists.")
                run_flag = True
                break

        return run_flag

    def run_all(self, max_num_function: int, function_dict: dict[str, list[str]], n_run: int=1):
        """Run the fuzzer on all functions in parallel."""

        with Pool(processes=self.config.num_processes) as pool:
            
            count = 0
            for i in range(max_num_function):
                for key in function_dict.keys():
                    if i >= len(function_dict[key]):
                        continue
                    function_signature = function_dict[key][i]
                    project_name = key

                    # Check if the function has already been run
                    if self.has_run(function_signature, project_name, n_run):
                        continue
                    port = self.config.ports[count % len(self.config.ports)]
                    config_with_port = BenchConfig(self.cfg_path)
                    # Update the base URL with the selected port
                    config_with_port.base_url = f"{self.config.base_url}:{port}"
                    pool.apply_async(Runner.run_one, args=(config_with_port, function_signature, project_name, n_run))

                    count += 1

            print(f"Iteration {n_run} of {self.config.iterations}: {count} functions to run")
 
            pool.close()
            pool.join()

    def run(self):
        """Run parallel execution with configuration from YAML file.
        
        Args:
            iterations (int, optional): Number of iterations, uses config value if None
        """
        # copy the config file to the save directory
        if not os.path.exists(self.config.save_root):
            os.makedirs(self.config.save_root)
        config_file = os.path.join(self.config.save_root, "config.yaml")
        with open(config_file, 'w') as f:
            yaml.dump(self.config, f)
        function_dicts = get_benchmark_functions(self.config.benchmark_dir,
                                                 allowed_projects=self.config.project_name if self.config.project_name else [],
                                                 allowed_langs=["c++", "c"],
                                                 allowed_functions=self.config.function_signatures,
                                                 funcs_per_project=self.config.funcs_per_project)

       
        start_time = time.time()
        for i in range(self.config.iterations):
            iter_res = self.config.save_root / "res_{}.txt".format(i+1)
            if iter_res.exists():
                print(f"Iteration {i+1} already completed. Skipping...")
                continue
            print(f"Running iteration {i+1} of {self.config.iterations}...")
            success_func = self.get_successful_func()
            todo_function_dicts = self.filter_functions(function_dicts, success_func)
            max_num_function, total_function = self.get_num_function(todo_function_dicts)

            if total_function == 0:
                print("All functions are successful. Exiting...")
                break
        
            self.run_all(max_num_function, todo_function_dicts, n_run=i+1)

            run_agent_res(self.config.save_root, semantic_mode="eval", n_run=i+1)

        print(f"Total time taken: {time.time()-start_time:.2f} seconds")

    # def run_single(self):
    #     """Run single execution with configuration from YAML file."""

    #     assert len(self.config.function_signatures) > 0, "No function signatures provided in the config file."
    #     assert self.config.project_name is not None, "No project name provided in the config file."

    #     iterations_list = self.config.get('iterations_list', [1])

    #     for function_signature in self.config.function_signatures:
    #         for i in iterations_list:
    #             Runner.run_one(self.config, function_signature, self.config.project_name, n_run=i)



if __name__ == "__main__":
    # # Check if the script is being run directly
    # if len(sys.argv) < 2:
    #     print("Usage: python run.py <config_path>")
    #     sys.exit(1)

    # 
    cfg_list= [
       
        # "/home/yk/code/LLM-reasoning-agents/cfg/gpt5_mini_header_no.yaml",
        #  "/home/yk/code/LLM-reasoning-agents/cfg/gpt5_mini_header_agent.yaml",
        #  "/home/yk/code/LLM-reasoning-agents/cfg/gpt5_mini_header_oss_fuzz.yaml",
        # "/home/yk/code/LLM-reasoning-agents/cfg/claude_code_info_oss_fuzz.yaml",
        # "/home/yk/code/LLM-reasoning-agents/cfg/gpt5_mini_example_public_rank.yaml",
        # "/home/yk/code/LLM-reasoning-agents/cfg/gpt5_mini/gpt5_mini_agent_wild.yaml",
        # "/home/yk/code/LLM-reasoning-agents/cfg/gpt5_mini/gpt5_mini_raw_wild.yaml",
         "/home/yk/code/LLM-reasoning-agents/cfg/gpt5_mini/gpt5_mini_issta_wild.yaml",
        # "/home/yk/code/LLM-reasoning-agents/cfg/gpt5_mini_code_info_agent.yaml",
        # "/home/yk/code/LLM-reasoning-agents/cfg/gpt5_mini_code_info_oss_fuzz.yaml",
        #   "/home/yk/code/LLM-reasoning-agents/cfg/gpt5_mini_example_project_random.yaml",
        # "/home/yk/code/LLM-reasoning-agents/cfg/deepseek_header_oss_fuzz.yaml"
        # "/home/yk/code/LLM-reasoning-agents/cfg/wild_gpt41_code_info_agent.yaml"
    ]
    for config_path in cfg_list:
        runner = Runner(config_path)

        # Set up signal handling for graceful termination6
        def signal_handler(sig, frame): # type: ignore
            print('Exiting gracefully...')
            sys.exit(0)

        signal.signal(signal.SIGINT, signal_handler) # type: ignore
        
        # Run the main function
        runner.run()