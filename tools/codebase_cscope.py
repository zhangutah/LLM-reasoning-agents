import subprocess
import traceback
import os
import sys
import re

# for debugging only
PROJECT_NAME = "libucl" if len(sys.argv) < 2 else sys.argv[1]
SAVED_FILENAME = "result.c"
TARGET_LANG = "C"
DEBUG = False


def extract_cpp_code_blocks(markdown_text):
    # This regular expression matches text between ```cpp and ```
    pattern = re.compile(r'```cpp([\n\r ]+.+)```', re.DOTALL) if TARGET_LANG == "C++" else re.compile(
        r'```c([\n\r ]+.+)```', re.DOTALL)
    matches = pattern.findall(markdown_text)
    return matches


def run_cscope(command, cscope_db):
    result = subprocess.run(["cscope", "-dL", "-f", cscope_db] + command,
                            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    if result.returncode != 0:
        raise Exception(f"cscope error: {result.stderr}")
    return result.stdout.strip()


def run_grep_c2(pattern):
    result = subprocess.run(["grep", pattern, "-I", "-n", "-H", "-R", ".", "-C2"],
                            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    if result.returncode != 0:
        raise Exception(f"grep error: {result.stderr}")
    return result.stdout.strip()[:1000]


def get_function_scope(filename, start_line):
    scope = []
    with open(filename, 'r') as file:
        lines = file.readlines()
        curly_brace_count = 0
        for line in lines[start_line - 1:]:
            if '{' in line:
                curly_brace_count += line.count('{')
            if '}' in line:
                curly_brace_count -= line.count('}')
            scope.append(line.strip())
            if curly_brace_count == 0:
                break
    return ''.join(scope)


def testing_cscope(function_name, cscope_db):
    definition = run_cscope(["-1", f"{function_name}"], cscope_db)
    callers = run_cscope(["-3", f"{function_name}"], cscope_db)
    caller_file = callers.split('\n')[0].split(' ')[0]
    if len(callers) < 1:
        print(
            f"[ERR] Cannot find ther caller to target function, discard this function!")
        return None

    caller_0_name = callers.split('\n')[0].split()[1]
    print(f"[DBG] Caller's name:\t{caller_0_name}")
    caller_code = open(caller_file, 'r').read()
    index_caller_0_start = caller_code.index(caller_0_name) - 40 if caller_code.index(
        caller_0_name) > 40 else caller_code.index(caller_0_name)

    caller_code = caller_code[index_caller_0_start:index_caller_0_start+3000] if len(
        caller_code) > 3000 else caller_code

    context_code = run_grep_c2(function_name)

    prompt = f"""
Here is one of the caller code to help you identify the necessary .h files and variables:
```
{caller_code}
```

Here are some code snippets to help you identify the necessary context for building the harness:

```
{context_code}
```

Here are some code snippets to help you set up the parameter for calling the target function:
"""
    print(prompt)
    # pass
