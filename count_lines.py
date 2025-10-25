#!/usr/bin/env python3
"""
Utility script to count total lines of code in the repository.
"""

import os
from pathlib import Path
from collections import defaultdict
from typing import Dict


def count_lines_by_extension(root_dir: Path, exclude_dirs: set = None) -> Dict[str, Dict[str, int]]:
    """
    Count lines of code by file extension.
    
    Args:
        root_dir: Root directory to start counting from
        exclude_dirs: Set of directory names to exclude
    
    Returns:
        Dictionary mapping file extensions to line counts
    """
    if exclude_dirs is None:
        exclude_dirs = {'.git', '__pycache__', 'node_modules', '.vscode', 'venv', 'env'}
    
    stats = defaultdict(lambda: {'files': 0, 'lines': 0, 'blank': 0, 'comment': 0, 'code': 0})
    
    for root, dirs, files in os.walk(root_dir):
        # Remove excluded directories from dirs to prevent walking into them
        dirs[:] = [d for d in dirs if d not in exclude_dirs]
        
        for file in files:
            filepath = Path(root) / file
            ext = filepath.suffix.lower()
            
            if not ext:
                continue
            
            try:
                with open(filepath, 'r', encoding='utf-8', errors='ignore') as f:
                    lines = f.readlines()
                    total_lines = len(lines)
                    blank_lines = sum(1 for line in lines if line.strip() == '')
                    
                    # Simple comment detection for common file types
                    # Note: This is a simple heuristic and may not catch all cases
                    comment_lines = 0
                    if ext in ['.py']:
                        comment_lines = sum(1 for line in lines if line.strip().startswith('#'))
                    elif ext in ['.c', '.cpp', '.cc', '.cxx', '.h', '.hpp', '.java']:
                        comment_lines = sum(1 for line in lines if line.strip().startswith('//'))
                    
                    code_lines = total_lines - blank_lines - comment_lines
                    
                    stats[ext]['files'] += 1
                    stats[ext]['lines'] += total_lines
                    stats[ext]['blank'] += blank_lines
                    stats[ext]['comment'] += comment_lines
                    stats[ext]['code'] += code_lines
            except Exception as e:
                print(f"Error reading {filepath}: {e}")
                continue
    
    return dict(stats)


def print_statistics(stats: Dict[str, Dict[str, int]]) -> None:
    """Print line count statistics in a formatted table."""
    print("\n" + "="*80)
    print("Code Statistics by File Type")
    print("="*80)
    print(f"{'Extension':<15} {'Files':>10} {'Total Lines':>15} {'Blank':>10} {'Code':>10}")
    print("-"*80)
    
    total_files = 0
    total_lines = 0
    total_blank = 0
    total_code = 0
    
    # Sort by total lines descending
    sorted_stats = sorted(stats.items(), key=lambda x: x[1]['lines'], reverse=True)
    
    for ext, counts in sorted_stats:
        print(f"{ext:<15} {counts['files']:>10} {counts['lines']:>15} {counts['blank']:>10} {counts['code']:>10}")
        total_files += counts['files']
        total_lines += counts['lines']
        total_blank += counts['blank']
        total_code += counts['code']
    
    print("-"*80)
    print(f"{'TOTAL':<15} {total_files:>10} {total_lines:>15} {total_blank:>10} {total_code:>10}")
    print("="*80)
    print(f"\nTotal lines of code: {total_code}")
    print(f"Total lines (including blanks): {total_lines}")
    print()


def main():
    """Main function to count and display line statistics."""
    root_dir = Path(__file__).parent
    stats = count_lines_by_extension(root_dir)
    print_statistics(stats)


if __name__ == "__main__":
    main()
