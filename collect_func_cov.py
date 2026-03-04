#!/usr/bin/env python3
"""Collect unique covered functions from COVERED_FUNC coverage logs."""

import argparse
import glob
import os
import re
import sys

# COVERED_FUNC: hits: 429 edges: 12/17 FuncName /path/to/file.cxx:68
LINE_RE = re.compile(
    r"^COVERED_FUNC:\s+hits:\s+(\d+)\s+edges:\s+(\d+)/(\d+)\s+(.+)\s+(/\S+:\d+)$"
)


def parse_line(line):
    """Return (func_name, edges_covered, edges_total, source_loc) or None."""
    m = LINE_RE.match(line.strip())
    if not m:
        return None
    hits = int(m.group(1))
    edges_covered = int(m.group(2))
    edges_total = int(m.group(3))
    func_name = m.group(4).strip()
    source_loc = m.group(5)
    return func_name, edges_covered, edges_total, source_loc


def is_blocked(func_name, blocked_ns):
    """Check if function belongs to a blocked namespace."""
    for ns in blocked_ns:
        if func_name.startswith(ns + "::"):
            return True
    return False


def collect(log_dir, blocked_ns):
    """Scan all *_coverage.log files, return deduplicated function records."""
    pattern = os.path.join(log_dir, "**", "*_coverage.log")
    log_files = sorted(glob.glob(pattern, recursive=True))

    if not log_files:
        print(f"No *_coverage.log files found under {log_dir}", file=sys.stderr)
        sys.exit(1)

    # key: (func_name, source_loc) -> (edges_covered, edges_total)
    # keep the max edges_covered across duplicate entries
    funcs = {}

    for path in log_files:
        with open(path) as f:
            for line in f:
                rec = parse_line(line)
                if rec is None:
                    continue
                func_name, edges_covered, edges_total, source_loc = rec
                if is_blocked(func_name, blocked_ns):
                    continue
                key = (func_name, source_loc)
                prev = funcs.get(key)
                if prev is None or edges_covered > prev[0]:
                    funcs[key] = (edges_covered, edges_total)

    return funcs, log_files


def main():
    parser = argparse.ArgumentParser(
        description="Collect unique covered functions from coverage logs."
    )
    parser.add_argument("log_dir", help="Directory containing coverage logs (searched recursively)")
    parser.add_argument(
        "blocked_namespaces",
        nargs="?",
        default="",
        help="Comma-separated blocked namespaces, e.g. 'std,__gnu_cxx'",
    )
    args = parser.parse_args()

    blocked_ns = [ns.strip() for ns in args.blocked_namespaces.split(",") if ns.strip()]

    funcs, log_files = collect(args.log_dir, blocked_ns)

    total_edges_covered = 0
    total_edges = 0
    for (edges_covered, edges_total) in funcs.values():
        total_edges_covered += edges_covered
        total_edges += edges_total

    print(f"Log files scanned     : {len(log_files)}")
    print(f"Blocked namespaces    : {blocked_ns if blocked_ns else '(none)'}")
    print(f"Unique functions hit  : {len(funcs)}")
    print(f"Total edges covered   : {total_edges_covered} / {total_edges}"
          f"  ({total_edges_covered / total_edges * 100:.1f}%)" if total_edges else "")
    print()
    print(f"{'Edges':>12}  {'Function':<60}  Source")
    print("-" * 120)
    for (func_name, source_loc), (ec, et) in sorted(funcs.items(), key=lambda x: x[0][1]):
        print(f"  {ec:>4}/{et:<4}    {func_name:<60}  {source_loc}")


if __name__ == "__main__":
    main()
