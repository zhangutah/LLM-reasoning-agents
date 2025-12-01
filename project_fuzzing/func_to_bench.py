#!/usr/bin/env python3
"""
Convert function signatures to benchmark YAML format.

Usage:
    python convert_functions_to_benchmark.py <project_name> [--threshold <score>]

Example:
    python convert_functions_to_benchmark.py libssh --threshold 7
"""

import json
import sys
import argparse
import yaml
from pathlib import Path
from typing import Any

def load_functions_scored(project_name: str, project_dir: str):
    """Load functions_scored JSON file for a given project."""
    project_path = Path(project_dir) / project_name
    
    if not project_path.exists():
        raise FileNotFoundError(f"Project directory not found: {project_path}")
    
    # Find the functions_scored file
    scored_files = list(project_path.glob("functions_scored_*.json"))
    
    if not scored_files:
        raise FileNotFoundError(f"No functions_scored file found in {project_path}")
    
    if len(scored_files) > 1:
        print(f"Warning: Multiple functions_scored files found. Using: {scored_files[0]}")
    
    with open(scored_files[0], 'r') as f:
        data = json.load(f)
    
    return data


def load_symbol_signatures(project_name: str, cache_dir: str) -> dict[str, Any]:
    """Load symbol signatures from cache directory."""
    # Try both lsp and parser files
    lsp_file = Path(cache_dir) / project_name / "All_all_symbols_lsp.json"
    parser_file = Path(cache_dir) / project_name / "All_all_symbols_parser.json"
    
    symbols: dict[str, Any] = {}
    
    # Try LSP file first
    if lsp_file.exists() and lsp_file.stat().st_size > 0:
        with open(lsp_file, 'r') as f:
            try:
                data = json.load(f)
                if isinstance(data, list):
                    for item in data: # type: ignore
                        if 'name' in item and 'signature' in item:
                            symbols[item['name']] = item
            except json.JSONDecodeError:
                print(f"Warning: Could not parse {lsp_file}")
    
    # Try parser file if LSP is empty
    if not symbols and parser_file.exists() and parser_file.stat().st_size > 0:
        with open(parser_file, 'r') as f:
            try:
                data = json.load(f)
                if isinstance(data, list):
                    for item in data: # type: ignore
                        if 'name' in item and 'signature' in item:
                            symbols[item['name']] = item
            except json.JSONDecodeError:
                print(f"Warning: Could not parse {parser_file}")
    
    return symbols


def parse_signature_to_params(signature: str) -> list[dict[str, str]]:
    """Parse a C function signature to extract parameters."""
    # This is a simplified parser - may need enhancement for complex signatures
    params: list[dict[str, str]] = []
    
    if not signature or '(' not in signature:
        return params
    
    try:
        # Extract the part between parentheses
        param_str = signature.split('(', 1)[1].rsplit(')', 1)[0].strip()
        
        if not param_str or param_str == 'void':
            return params
        
        # Split by comma (naive approach - doesn't handle function pointers well)
        param_parts: list[str] = []
        depth = 0
        current = ""
        
        for char in param_str:
            if char in '([':
                depth += 1
                current += char
            elif char in ')]':
                depth -= 1
                current += char
            elif char == ',' and depth == 0:
                param_parts.append(current.strip())
                current = ""
            else:
                current += char
        
        if current.strip():
            param_parts.append(current.strip())
        
        # Parse each parameter
        for param in param_parts:
            param = param.strip()
            if not param:
                continue
            
            # Split into type and name (last token is usually the name)
            tokens = param.split()
            if len(tokens) == 0:
                continue
            elif len(tokens) == 1:
                # Only type, no name
                params.append({
                    'name': '',
                    'type': tokens[0]
                })
            else:
                # Last token is the name, rest is type
                name = tokens[-1]
                # Remove array brackets or pointer from name
                if '[' in name:
                    name = name.split('[')[0]
                name = name.lstrip('*')
                
                param_type = ' '.join(tokens[:-1])
                params.append({
                    'name': name,
                    'type': param_type
                })
    
    except Exception as e:
        print(f"Warning: Failed to parse signature '{signature}': {e}")
    
    return params


def extract_return_type(signature : str) -> str:
    """Extract return type from function signature."""
    if not signature or '(' not in signature:
        return 'void'
    
    # Get everything before the function name and parameters
    before_params = signature.split('(')[0].strip()
    
    # Split by spaces and take everything except the last token (function name)
    tokens = before_params.split()
    if len(tokens) <= 1:
        return 'void'
    
    # Return type is everything except the last token
    return ' '.join(tokens[:-1])


def convert_to_benchmark_format(functions: list[dict[str, Any]], symbols: dict[str, Any], threshold: float = 7.0) -> list[dict[str, Any]]:
    """Convert functions with their signatures to benchmark YAML format."""
    benchmark_functions: list[dict[str, Any]] = []
    
    for func in functions:
        score = func.get('score', 0)
        
        if score <= threshold:
            continue
        
        func_name = func.get('name', '')
        clean_name = func.get('clean_name', '')
        
        # Try to find signature in symbols
        signature = None
        symbol_data = None
        
        # Try different name variations
        for name_variant in [clean_name, func_name, clean_name.split('::')[-1]]:
            if name_variant in symbols:
                symbol_data = symbols[name_variant]
                signature = symbol_data.get('signature', '')
                break
        
        if not signature:
            print(f"Warning: No signature found for {clean_name} (score: {score})")
            continue
        
        # Parse signature
        params = parse_signature_to_params(signature)
        return_type = extract_return_type(signature)
        
        benchmark_func: dict[str, Any] = {
            'name': clean_name,
            'params': params,
            'return_type': return_type,
            'signature': signature.strip()
        }
        
        benchmark_functions.append(benchmark_func)
    
    return benchmark_functions


def save_benchmark_yaml(project_name: str, functions: list[dict[str, Any]], output_dir: str) -> Path:
    """Save benchmark functions to YAML file."""
    output_path = Path(output_dir) / project_name / f"{project_name}.yaml"
    output_path.parent.mkdir(parents=True, exist_ok=True)
    
    data = {'functions': functions}
    
    with open(output_path, 'w') as f:
        yaml.dump(data, f, default_flow_style=False, sort_keys=False, allow_unicode=True)
    
    print(f"Saved {len(functions)} functions to {output_path}")
    return output_path


def main():
    parser = argparse.ArgumentParser(
        description='Convert function signatures to benchmark YAML format'
    )
    parser.add_argument('project_name', help='Project name (e.g., libssh)')
    parser.add_argument('--threshold', type=float, default=7.0,
                       help='Minimum score threshold (default: 7.0)')
    parser.add_argument('--project-dir', default='/home/yk/code/LLM-reasoning-agents/project_fuzzing/projects',
                       help='Project directory path')
    parser.add_argument('--cache-dir', default='/home/yk/code/LLM-reasoning-agents/cache',
                       help='Cache directory path')
    parser.add_argument('--output-dir', default='/home/yk/code/LLM-reasoning-agents/benchmark-sets/projects',
                       help='Output directory path')
    
    args = parser.parse_args()
    
    try:
        print(f"Processing project: {args.project_name}")
        print(f"Score threshold: {args.threshold}")
        print()
        
        # Step 1: Load functions_scored JSON
        print("Step 1: Loading functions_scored data...")
        scored_data = load_functions_scored(args.project_name, args.project_dir)
        total_functions = len(scored_data.get('functions', []))
        print(f"  Found {total_functions} functions")
        
        # Step 2: Load symbol signatures
        print("\nStep 2: Loading symbol signatures...")
        symbols = load_symbol_signatures(args.project_name, args.cache_dir)
        print(f"  Loaded {len(symbols)} symbol signatures")
        
        # Step 3: Filter and convert functions
        print(f"\nStep 3: Filtering functions with score > {args.threshold}...")
        benchmark_functions = convert_to_benchmark_format(
            scored_data.get('functions', []),
            symbols,
            args.threshold
        )
        print(f"  Converted {len(benchmark_functions)} functions")
        
        if not benchmark_functions:
            print("\n⚠ Warning: No functions matched the criteria.")
            print("   Possible reasons:")
            print("   - No symbol signatures found in cache directory")
            print("   - All functions are below the threshold score")
            print("   - Function names in scored data don't match symbol names")
            return
        
        # Step 4: Save to YAML
        print("\nStep 4: Saving to YAML...")
        output_path = save_benchmark_yaml(
            args.project_name,
            benchmark_functions,
            args.output_dir
        )
        
        print(f"\n✓ Success! Benchmark file created at: {output_path}")
        print(f"  Total functions: {len(benchmark_functions)}")
        print(f"  From {total_functions} total functions in project")
        
    except FileNotFoundError as e:
        print(f"Error: {e}", file=sys.stderr)
        sys.exit(1)
    except Exception as e:
        print(f"Error: {e}", file=sys.stderr)
        import traceback
        traceback.print_exc()
        sys.exit(1)


if __name__ == '__main__':
    main()
