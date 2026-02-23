#!/usr/bin/env python3
"""Collect final coverage from evaluation results into a TSV file.

Usage:
    python collect_coverage.py <eval_dir> [-o output.tsv]

Example:
    python collect_coverage.py outputs_cpp/gpt5-mini/evaluation_bloaty/bloaty
"""

import argparse
import os
import re
import sys
from pathlib import Path


def parse_final_coverage(cov_path: Path) -> int | None:
    """Extract 'Final coverage' value from a cov.txt file."""
    try:
        text = cov_path.read_text()
        m = re.search(r"Final coverage:\s*(\d+)", text)
        if m:
            return int(m.group(1))
    except Exception:
        pass
    return None


def read_signature(func_path: Path) -> str | None:
    """Read the function signature from function.txt."""
    try:
        return func_path.read_text().strip()
    except Exception:
        return None


def collect(eval_dir: str, output: str | None = None):
    root = Path(eval_dir)
    if not root.is_dir():
        print(f"Error: {eval_dir} is not a directory", file=sys.stderr)
        sys.exit(1)

    rows: list[tuple[str, int]] = []

    for cov_path in sorted(root.rglob("cov.txt")):
        run_dir = cov_path.parent
        func_path = run_dir / "function.txt"

        coverage = parse_final_coverage(cov_path)
        signature = read_signature(func_path)

        if coverage is None:
            print(f"Warning: no 'Final coverage' in {cov_path}", file=sys.stderr)
            continue
        if signature is None:
            print(f"Warning: no function.txt in {run_dir}", file=sys.stderr)
            continue

        rows.append((signature, coverage))

    # Sort by signature for deterministic output
    rows.sort(key=lambda r: r[0])

    out = sys.stdout if output is None else open(output, "w")
    try:
        for sig, cov in rows:
            out.write(f"{sig}\t{cov}\n")
    finally:
        if out is not sys.stdout:
            out.close()

    print(f"Collected {len(rows)} entries", file=sys.stderr)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Collect final coverage from evaluation results into TSV")
    parser.add_argument("eval_dir", help="Directory containing evaluation results")
    parser.add_argument("-o", "--output", help="Output TSV file (default: stdout)")
    args = parser.parse_args()
    collect(args.eval_dir, args.output)
