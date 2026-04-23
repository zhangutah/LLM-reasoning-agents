#!/usr/bin/env python3
"""
Compile and run LLM-generated fuzzing harnesses for validation.

Reads .c harness files from GeneralAgentHarnessGen output directory,
compiles each via oss-fuzz Docker infrastructure, and runs the fuzzer
for a specified duration (default 1 minute) to verify correctness.

Output format is compatible with:
  - agent_tools/results_analysis.py  (run_agent_res / collect_run_info)
  - agent/eval.py                    (run_evaluation / process_single_result)

Output layout per harness:
    save_dir/{project}/{function}/run{n}_{random}/
        function.txt      - function signature
        harness.txt        - harness source code
        fuzzer_info.json   - {"fuzzer_name": ..., "fuzzer_path": ...}
        agent.log          - log with "Fuzz res:No Error" or error info
        fuzzing0.log       - raw fuzzer output (from FuzzerRunner)

Output summary files:
    save_dir/success_functions_{n_run}.json   - for eval.py run_evaluation()
    save_dir/eval_results.json                - detailed per-harness results

Usage:
    python codeagent/gen.py \\
        --results_dir /home/yk/code/GeneralAgentHarnessGen/outputs/claude_haiku/results \\
        --run_time 1

    # Then analyze results with results_analysis.py:
    #   run_agent_res(save_dir, semantic_mode="gen", n_run=1)
    #
    # Then run coverage evaluation with eval.py:
    #   run_evaluation(save_dir, benchcfg, n_run=1)
"""

import json
import logging
import os
import sys
import yaml
import random
import shutil
import multiprocessing
import psutil
from pathlib import Path
from argparse import ArgumentParser
from typing import Any

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from agent_tools.fuzz_tools.run_fuzzer import FuzzerRunner
from agent_tools.fuzz_tools.compiler import Compiler
from agent.modules.semantic_check import SemaCheck
from utils.misc import function_dir_name
from utils.oss_fuzz_utils import OSSFuzzUtils
from utils.docker_utils import DockerUtils
from constants import CompileResults, ValResult, LanguageType, PROJECT_PATH


def load_benchmark_info(benchmark_dir: Path) -> dict[str, dict[str, Any]]:
    """Load benchmark YAML files indexed by project name.
    Each YAML provides target_name (fuzzer name) and target_path (harness path in container).
    """
    info: dict[str, dict[str, Any]] = {}
    for yf in benchmark_dir.glob("*.yaml"):
        with open(yf) as f:
            data = yaml.safe_load(f)
        info[data.get("project", yf.stem)] = data
    return info


def scan_results(results_dir: Path, benchmark_info: dict[str, dict[str, Any]]) -> list[dict[str, Any]]:
    """Scan GeneralAgentHarnessGen results directory for harness .c files.

    Expected layout:
        results_dir/{project}/{function}/{function}_harness.c
        results_dir/{project}/{function}/{project}_{function}.json  (optional)

    If the .json metadata is missing, project/function names are derived from
    directory names and function_signature is looked up from benchmark YAML.
    """
    harnesses: list[dict[str, Any]] = []
    for project_dir in sorted(results_dir.iterdir()):
        if not project_dir.is_dir():
            continue
        for func_dir in sorted(project_dir.iterdir()):
            if not func_dir.is_dir():
                continue
            c_files = list(func_dir.glob("*_harness.c"))
            if not c_files:
                continue

            # Try to read metadata from JSON if available
            json_files = list(func_dir.glob("*.json"))
            if json_files:
                with open(json_files[0]) as f:
                    meta = json.load(f)
                project = meta.get("project", project_dir.name)
                func_name = meta.get("function_name", func_dir.name)
                func_sig = meta.get("function_signature", "")
            else:
                # Derive from directory names and benchmark YAML
                project = project_dir.name
                func_name = func_dir.name
                func_sig = ""
                bench = benchmark_info.get(project, {})
                for fn in bench.get("functions", []):
                    if fn.get("name", "") == func_name:
                        func_sig = fn.get("signature", "")
                        break

            harnesses.append({
                "project": project,
                "function_name": func_name,
                "function_signature": func_sig,
                "harness_file": c_files[0],
            })
    return harnesses


def _setup_work_dir_logger(work_dir: Path, name: str) -> logging.Logger:
    """Create a logger that writes to work_dir/agent.log (same format as FuzzENV)."""
    logger = logging.getLogger(name)
    logger.setLevel(logging.INFO)
    # Avoid duplicate handlers if called multiple times
    if logger.handlers:
        return logger
    log_file = work_dir / "agent.log"
    fh = logging.FileHandler(log_file)
    fh.setLevel(logging.INFO)
    formatter = logging.Formatter("%(asctime)s - %(levelname)s - %(message)s (%(filename)s:%(lineno)d)")
    fh.setFormatter(formatter)
    logger.addHandler(fh)
    return logger


def _cleanup(oss_fuzz_dir: Path, project_name: str, new_project_name: str) -> None:
    """Remove workspace directory, Docker image, and build artifacts."""
    try:
        oss_tool = OSSFuzzUtils(oss_fuzz_dir, Path(""), project_name, new_project_name)
        lang = oss_tool.get_project_language()
        docker_tool = DockerUtils(oss_fuzz_dir, project_name, new_project_name, lang)
        docker_tool.clean_build_dir()
        docker_tool.remove_image()
    except Exception:
        pass
    for p in [
        oss_fuzz_dir / "projects" / new_project_name,
        oss_fuzz_dir / "build" / "out" / new_project_name,
    ]:
        if p.exists():
            shutil.rmtree(p, ignore_errors=True)


def process_harness(
    args: tuple[dict[str, Any], dict[str, dict[str, Any]], Path, Path, str, int, int, bool, bool],
) -> dict[str, str]:
    """Compile and run a single harness. Returns a result dict.

    Produces a work directory compatible with results_analysis.py and eval.py:
        save_dir/{project}/{function}/run{n_run}_{rand}/
            function.txt, harness.txt, fuzzer_info.json, agent.log
    """
    harness_info, benchmark_info, oss_fuzz_dir, benchmark_dir, save_dir, run_time, n_run, ignore_crashes, semantic_check = args
    project_name: str = harness_info["project"]
    function_name: str = harness_info["function_name"]
    function_signature: str = harness_info["function_signature"]
    harness_file: Path = harness_info["harness_file"]

    result: dict[str, str] = {
        "project": project_name,
        "function": function_name,
        "function_signature": function_signature,
        "compile": "FAIL",
        "run": "N/A",
        "semantic": "N/A",
        "detail": "",
        "work_dir": "",
    }

    # Look up benchmark YAML for target_name and target_path
    if project_name not in benchmark_info:
        result["detail"] = "No benchmark YAML"
        print(f"[SKIP] {project_name}/{function_name}: no benchmark YAML")
        return result

    bench = benchmark_info[project_name]
    fuzzer_name: str = bench["target_name"]
    harness_path = Path(bench["target_path"])
    harness_code = harness_file.read_text()

    # Use run{n}_{random} naming to match FuzzENV / collect_run_info expectations
    rand_str = "".join(random.choices("abcdefghijklmnopqrstuvwxyz", k=16))
    new_project_name = f"run{n_run}_{rand_str}"

    # Use hash-suffixed dir name to match FuzzENV scheme so overloads don't collide.
    # Fall back to bare function_name when signature is missing (no way to disambiguate).
    if function_signature:
        func_dir = function_dir_name(function_signature, language=LanguageType.CPP)
    else:
        func_dir = function_name.lower()
    func_save = Path(save_dir) / project_name.lower() / func_dir / new_project_name
    func_save.mkdir(parents=True, exist_ok=True)
    result["work_dir"] = str(func_save)

    # --- Write metadata files expected by downstream tools ---
    # function.txt: required by collect_run_info and get_run_res
    (func_save / "function.txt").write_text(function_signature)
    # harness.txt: required by get_run_res (semantic check) and process_single_result
    (func_save / "harness.txt").write_text(harness_code)
    # fuzzer_info.json: required by process_single_result in eval.py
    with open(func_save / "fuzzer_info.json", "w") as f:
        json.dump({"fuzzer_name": fuzzer_name, "fuzzer_path": str(harness_path)}, f, indent=2)

    logger = _setup_work_dir_logger(func_save, new_project_name)
    logger.info(f"Function: {function_signature}")
    logger.info(f"Project: {project_name}, workspace: {new_project_name}")

    try:
        # --- Copy project to create isolated workspace ---
        dst = oss_fuzz_dir / "projects" / new_project_name
        src = oss_fuzz_dir / "projects" / project_name
        if not src.exists():
            result["detail"] = f"Project dir not found: {src}"
            logger.error(f"Project dir not found: {src}")
            print(f"[SKIP] {project_name}/{function_name}: project dir missing")
            return result
        if dst.exists():
            shutil.rmtree(dst)
        shutil.copytree(src, dst, dirs_exist_ok=True)
        harness_pair_path = "/home/yk/code/LLM-reasoning-agents/cache/{}/harness_fuzzer_pairs.json".format(project_name)
        
        with open(harness_pair_path, "r") as f:
            harness_pair = json.load(f)

        for fuzzer_name, harness_path in {k: Path(v) for k, v in harness_pair.items()}.items():
            # --- Compile harness ---
            # Compiler handles: Dockerfile modification, Docker image build, fuzzer build
            logger.info(f"Compiling harness for {fuzzer_name}")
            compiler = Compiler(oss_fuzz_dir, benchmark_dir, project_name, new_project_name)
            compile_res, compile_msg = compiler.compile_harness(
                harness_code=harness_code,
                harness_path=harness_path,
                fuzzer_name=fuzzer_name,
            )

            # If compile fails and harness doesn't include fuzz.h, retry with it
            if compile_res != CompileResults.Success and '#include "fuzz.h"' not in harness_code:
                logger.info("Compile failed, retrying with #include \"fuzz.h\"")
                new_harness_code = '#include "fuzz.h"\n' + harness_code
                compile_res, compile_msg = compiler.compile_harness(
                    harness_code=new_harness_code,
                    harness_path=harness_path,
                    fuzzer_name=fuzzer_name,
                )
                if compile_res == CompileResults.Success:
                    logger.info("Retry with fuzz.h succeeded")
                    # Update saved harness.txt with the fixed version
                    (func_save / "harness.txt").write_text(new_harness_code)

            if compile_res != CompileResults.Success:
                result["detail"] = f"{compile_res.value}"
                logger.error(f"Fuzzer compilation failed: {compile_res.value}")
                logger.error(f"Compile output: {compile_msg}")
                print(f"[COMPILE FAIL] {project_name}/{function_name}: {compile_res.value}")
                # print(f"  Compile log:\n{compile_msg[-2000:]}")
                continue

            result["compile"] = "OK"
            logger.info(f"Compilation succeeded for {fuzzer_name}")
            print(f"[COMPILE OK] {project_name}/{function_name}")

            # --- Run fuzzer ---
            oss_tool = OSSFuzzUtils(oss_fuzz_dir, benchmark_dir, project_name, new_project_name)
            project_lang = oss_tool.get_project_language()

            fuzzer = FuzzerRunner(
                oss_fuzz_dir=oss_fuzz_dir,
                new_project_name=new_project_name,
                project_lang=project_lang,
                run_timeout=run_time,
                save_dir=func_save,
            )
            fuzz_res, _, _ = fuzzer.run_fuzzing(
                counter=0,
                fuzzer_name=fuzzer_name,
                ignore_crashes=ignore_crashes,
                no_log=False,
            )

            if fuzz_res == ValResult.NoError:
                result["run"] = "OK"
                # This pattern is checked by get_run_res in results_analysis.py
                logger.info(f"Fuzz res:{ValResult.NoError.value}")
                print(f"[RUN OK] {project_name}/{function_name}")
            else:
                result["run"] = "FAIL"
                result["detail"] = fuzz_res.value
                logger.error(f"Fuzz res:{fuzz_res.value}")
                print(f"[RUN FAIL] {project_name}/{function_name}: {fuzz_res.value}")

            # --- Semantic check (optional, runs after compile succeeds) ---
            if semantic_check and result["compile"] == "OK":
                logger.info(f"Running semantic check for {function_name}")
                sema = SemaCheck(
                    oss_fuzz_dir, benchmark_dir, project_name,
                    new_project_name, function_name, project_lang,
                )
                sema_passed = sema.check(harness_code, harness_path, fuzzer_name)
                if sema_passed:
                    result["semantic"] = "OK"
                    logger.info("Semantic check passed")
                    print(f"[SEMA OK] {project_name}/{function_name}")
                else:
                    result["semantic"] = "FAIL"
                    logger.error("Semantic check failed")
                    print(f"[SEMA FAIL] {project_name}/{function_name}")

            return result

    except Exception as e:
        result["detail"] = str(e)[:300]
        logger.error(f"Exception: {e}")
        print(f"[ERROR] {project_name}/{function_name}: {e}")
        return result
    finally:
        # Remove logger handlers to avoid leaking file handles
        for handler in logger.handlers[:]:
            handler.close()
            logger.removeHandler(handler)
        _cleanup(oss_fuzz_dir, project_name, new_project_name)


def main() -> None:
    parser = ArgumentParser(description="Compile and run LLM-generated fuzzing harnesses for validation.")
    parser.add_argument("--results_dir", type=str, required=True,
                        help="Path to GeneralAgentHarnessGen results directory containing project/function subdirs")
    parser.add_argument("--benchmark_dir", type=str,
                        default=os.path.join(PROJECT_PATH, "benchmark-sets", "ntu"),
                        help="Path to benchmark YAML directory (default: benchmark-sets/ntu)")
    parser.add_argument("--oss_fuzz_dir", type=str, default="/home/yk/code/oss-fuzz/",
                        help="Path to oss-fuzz repository")
    parser.add_argument("--save_dir", type=str,
                        default=os.path.join(PROJECT_PATH, "outputs", "codeagent", "claude-haiku"),
                        help="Directory to save evaluation logs and results")
    parser.add_argument("--run_time", type=int, default=1,
                        help="Fuzzing duration in minutes (default: 1)")
    parser.add_argument("--n_run", type=int, default=1,
                        help="Run number, used in directory naming run{n}_* and success_functions_{n}.json")
    parser.add_argument("--num_processes", type=int, default=None,
                        help="Number of parallel workers (default: 2/3 of physical cores)")
    parser.add_argument("--ignore_crashes", action="store_true",
                        help="Continue fuzzing even after crashes (uses -fork=1 mode)")
    parser.add_argument("--project", type=str, default=None,
                        help="Only evaluate harnesses for this project")
    parser.add_argument("--function", type=str, default=None,
                        help="Only evaluate this specific function")
    parser.add_argument("--semantic_check", action="store_true",
                        help="Run semantic check after successful compilation")
    args = parser.parse_args()

    oss_fuzz_dir = Path(args.oss_fuzz_dir)
    benchmark_dir = Path(args.benchmark_dir)
    if not benchmark_dir.is_absolute():
        benchmark_dir = Path(PROJECT_PATH) / benchmark_dir

    # Load benchmark info and scan results
    benchmark_info = load_benchmark_info(benchmark_dir)
    harnesses = scan_results(Path(args.results_dir), benchmark_info)

    # Apply filters
    if args.project:
        harnesses = [h for h in harnesses if h["project"] == args.project]
    if args.function:
        harnesses = [h for h in harnesses if h["function_name"] == args.function]

    if not harnesses:
        print("No harnesses found to evaluate.")
        return

    n_run: int = args.n_run
    print(f"Harnesses to evaluate: {len(harnesses)}")
    print(f"Benchmark projects loaded: {len(benchmark_info)}")
    print(f"Fuzzing duration: {args.run_time} minute(s), n_run: {n_run}")
    print(f"Save directory: {args.save_dir}")
    print("-" * 80)

    num_procs = args.num_processes or max(1, (psutil.cpu_count(logical=False) or 4) // 3 * 2)
    print(f"Using {num_procs} parallel workers")

    task_args = [
        (h, benchmark_info, oss_fuzz_dir, benchmark_dir, args.save_dir, args.run_time, n_run, args.ignore_crashes, args.semantic_check)
        for h in harnesses
    ]

    # Run evaluations in parallel
    with multiprocessing.Pool(processes=num_procs) as pool:
        results: list[dict[str, str]] = pool.map(process_harness, task_args)

    save_path = Path(args.save_dir)
    save_path.mkdir(parents=True, exist_ok=True)

    # --- Summary ---
    print("\n" + "=" * 110)
    print(f"{'PROJECT':<20} {'FUNCTION':<30} {'COMPILE':<10} {'RUN':<10} {'SEMA':<10} {'DETAIL'}")
    print("=" * 110)

    compile_ok = run_ok = sema_ok = total = 0
    for r in results:
        total += 1
        if r["compile"] == "OK":
            compile_ok += 1
        if r["run"] == "OK":
            run_ok += 1
        if r.get("semantic") == "OK":
            sema_ok += 1
        detail = r["detail"][:50] if r["detail"] else ""
        sema = r.get("semantic", "N/A")
        print(f"{r['project']:<20} {r['function']:<30} {r['compile']:<10} {r['run']:<10} {sema:<10} {detail}")

    print("=" * 110)
    print(f"Total: {total}  |  Compile OK: {compile_ok}/{total} ({compile_ok / total * 100:.1f}%)"
          f"  |  Run OK: {run_ok}/{total} ({run_ok / total * 100:.1f}%)"
          f"  |  Sema OK: {sema_ok}/{total} ({sema_ok / total * 100:.1f}%)")

    # Save detailed results JSON
    results_file = save_path / "eval_results.json"
    with open(results_file, "w") as f:
        json.dump(results, f, indent=2)

    print(f"\nDetailed results saved to: {results_file}")
    print(f"\nNext steps:")
    print(f"  # 1. Generate success_functions_{n_run}.json with results_analysis.py:")
    print(f"  run_agent_res(Path('{args.save_dir}'), semantic_mode='gen', n_run={n_run})")
    print(f"  # 2. Run coverage evaluation with eval.py:")
    print(f"  run_evaluation(Path('{args.save_dir}'), benchcfg, n_run={n_run})")


if __name__ == "__main__":
    main()
