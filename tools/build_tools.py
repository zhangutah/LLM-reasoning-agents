"""
This file contains tools to compile source code with clang.
"""
import subprocess
import os
import sys
import re

# for debugging only
COMPILE_CMD = "clang++ -c -g -fsanitize=address,fuzzer harness.c -I./include -I./src -o harness.o"
BUILD_SCRIPTS = {
    "compile": "compile_command.sh",
    "link": "link_command.sh"
}
PROMPT_LOG = ""

MAX_TRIES = 10
DEBUG = False


def extract_code_blocks(markdown_text):
    # This regular expression matches text between ```cpp and ```
    pattern = re.compile(r'```([\n\r ]+.+)```', re.DOTALL)
    matches = pattern.findall(markdown_text)
    return matches


def compile_c_code(cmd):
    result = subprocess.run(cmd.split(" "),
                            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    if result.returncode != 0:
        return False, result.stderr
    return True, result.stdout.strip()


def extract_first_line_starts_with_clang(code):
    lines = code.split("\n")
    for i, line in enumerate(lines):
        if line.startswith("clang"):
            return line
    return "NaN"


def save_compile_cmd():
    # Save the compile command to a file for later use
    filename = BUILD_SCRIPTS.get("compile", "compile_command.txt")
    with open(filename, 'a') as file:
        file.write(COMPILE_CMD)


def save_link_cmd():
    # Save the compile command to a file for later use
    filename = BUILD_SCRIPTS.get("compile", "link_command.txt")
    with open(filename, 'w') as file:
        file.write(COMPILE_CMD)


def main():
    global COMPILE_CMD
    if len(sys.argv) < 4:
        print("Usage: python3 build_tools.py <code_path> <prompt_log_path> <build_sh>")
        sys.exit(1)  # Exit with error

    # WIP
    pass
