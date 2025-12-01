#!/usr/bin/env python3
"""
Coverage Parser - Parse coverage.json and extract function information.

Usage:
    python coverage_parser.py coverage_output/libssh/coverage.json
    python coverage_parser.py coverage_output/libssh/coverage.json -o functions.json
    
Output: functions.json with all function coverage info
"""

import json
import sys
import subprocess
from pathlib import Path
from dataclasses import dataclass, asdict
from typing import Optional


@dataclass 
class FunctionInfo:
    """Function coverage information."""
    name: str
    clean_name: str
    source_file: str
    source_code: str
    line_start: int
    line_end: int
    execution_count: int
    covered_regions: int
    total_regions: int
    coverage_percent: float
    is_covered: bool
    is_static: bool
    
    def to_dict(self) -> dict:
        return asdict(self)


def parse_coverage(coverage_path: Path) -> list[FunctionInfo]:
    """Parse coverage JSON and extract function information."""
    functions = []
    
    with open(coverage_path) as f:
        coverage_data = json.load(f)
    
    for data_entry in coverage_data.get('data', []):
        for func in data_entry.get('functions', []):
            name = func.get('name', 'unknown')

            # if name.startswith('__sanitizer_'):
                # continue  # skip sanitizer internal functions
            if name.startswith('OSS_FUZZ_'):
                name = name[len('OSS_FUZZ_'):]  # strip prefix

            count = func.get('count', 0)
            filenames = func.get('filenames', ['unknown'])
            regions = func.get('regions', [])
            
            # Calculate coverage
            total_regions = len(regions)
            covered_regions = sum(1 for r in regions if r[4] > 0) if regions else 0
            coverage_pct = (covered_regions / total_regions * 100) if total_regions else 0.0
            
            # Get line range (primary file regions only)
            primary_regions = [r for r in regions if len(r) < 6 or r[5] == 0]
            if primary_regions:
                line_start = min(r[0] for r in primary_regions)
                line_end = max(r[2] for r in primary_regions)
            else:
                line_start = line_end = 0
            
          
            source_code = ""
            # Parse function name - handle "file.c:function_name" format (static functions)
            clean_name = name
            is_static = False
            if ':' in name and not name.startswith('operator'):
                parts = name.split(':', 1)
                if len(parts) == 2 and '.' in parts[0]:
                    is_static = True
                    clean_name = parts[1]

            # Demangle C++ symbols if they look mangled (Itanium ABI style "_Z...")
            if clean_name.startswith("_Z"):
                try:
                    # Use c++filt if available to demangle; fall back silently otherwise
                    result = subprocess.run(
                        ["c++filt", clean_name],
                        check=False,
                        stdout=subprocess.PIPE,
                        stderr=subprocess.DEVNULL,
                        text=True,
                    )
                    demangled = result.stdout.strip()
                    if demangled:
                        clean_name = demangled
                except Exception:
                    # If demangling fails for any reason, keep the original name
                    pass
            
            # filenames[0] is absolute path like /src/libssh/..., strip leading / to join properly
            rel_path = filenames[0].lstrip('/') if filenames[0].startswith("/") else ''
            true_file = coverage_path.parent / rel_path
            if true_file.exists():
                with open(true_file, 'r') as sf:
                    source_lines = sf.readlines()
                    # relocate line_start to actual function start (search backwards for function name)
                    for i in range(line_start-1, 0, -1):
                        if clean_name not in source_lines[i]:
                            continue
                        # found function name line
                        line_start = i  # convert to 1-indexed
                        break

                    source_code = "".join(source_lines[max(0, line_start-5):line_end])  # lines are 1-indexed
           
            functions.append(FunctionInfo(
                name=name,
                clean_name=clean_name,
                source_file=filenames[0] if filenames else 'unknown',
                line_start=line_start,
                line_end=line_end,
                source_code=source_code,
                execution_count=count,
                covered_regions=covered_regions,
                total_regions=total_regions,
                coverage_percent=round(coverage_pct, 2),
                is_covered=count > 0,
                is_static=is_static
            ))
    
    print(f"[+] Parsed {len(functions)} functions from coverage data.")
    return functions


def filter_functions(functions: list[FunctionInfo]) -> list[FunctionInfo]:
    """Filter functions based on criteria."""
    result = functions
    
    system_prefixes = ('/usr', '/lib', 'include/c++', 'include/llvm')
    result = [f for f in result 
                if not any(f.source_file.startswith(p) for p in system_prefixes)]
    
    # not start with /src/
    result = [f for f in result  if f.source_file.startswith('/src/')]
    
    # remove third party libraries
    third_party_indicators = ('third_party/', 'external/', 'bazel-', 'googlemock', 'googletest', 'gtest/', 'gmock/')
    result = [f for f in result 
                if not any(ind in f.source_file for ind in third_party_indicators)]
    return result


def save_functions(functions: list[FunctionInfo], 
                   output_path: Path,
                   project: str = "unknown") -> dict:
    """Save function info to JSON and return summary."""
    total = len(functions)
    covered = sum(1 for f in functions if f.is_covered)
    uncovered = total - covered
    avg_cov = sum(f.coverage_percent for f in functions) / total if total else 0
    
    # Sort by coverage (ascending - uncovered first)
    functions_sorted = sorted(functions, key=lambda f: f.coverage_percent)
    
    output = {
        'project': project,
        'statistics': {
            'total_functions': total,
            'covered_functions': covered,
            'uncovered_functions': uncovered,
            'coverage_rate': round(covered / total * 100, 2) if total else 0,
            'average_coverage': round(avg_cov, 2)
        },
        'functions': [f.to_dict() for f in functions_sorted]
    }
    
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with open(output_path, 'w') as f:
        json.dump(output, f, indent=2)
    
    return output['statistics']


def main():
    import argparse
    parser = argparse.ArgumentParser(description="Parse coverage JSON")
    parser.add_argument("--cov-json", default="/home/yk/code/oss-fuzz/build/out/libpng_cov/func_coverage.json", required=False, help="Path to coverage.json")
    parser.add_argument("--output", default="functions.json", required=False, help="Output JSON file path")
    parser.add_argument("--project", default="libpng", required=False, help="Project name for metadata")
    
    args = parser.parse_args()
    
    # Load coverage data
    coverage_path = Path(args.cov_json)
    if not coverage_path.exists():
        print(f"[-] File not found: {coverage_path}")
        sys.exit(1)
    
    # Infer project name from path if not provided
    project = args.project or coverage_path.parent.name
    
    # Parse and filter
    functions = parse_coverage(coverage_path)
    functions = filter_functions(functions)
    
    # Determine output path
    if args.output == "functions.json":
        output_path = coverage_path.parent / "functions.json"
    else:
        output_path = Path(args.output)
    
    # Save and print
    save_functions(functions, output_path, project)
    print(f"[+] Saved {len(functions)} functions to: {output_path}")


if __name__ == "__main__":
    main()
