FUNCTION_ANALYSIS_PROMPT = """
Analyze the given function signature and list each parameter with its data type and any initialization requirements.
Function signature:
```
{function_signature}
```
Your analysis should include the purpose of each parameter if known, and how it should be initialized for a valid test.
"""

FUZZING_HARNESS_GEN_PROMPT = """
Based on the function analysis below, generate a C++ fuzzing harness that initializes all parameters and calls the function.
- Use `FuzzedDataProvider` for input generation.
- Follow all provided guidelines strictly.
- Ensure all types match, and handle cases where initialization might fail gracefully.
Function analysis:
{function_analysis}

Guidelines:
{guidelines}

Function to fuzz:
```
{function_signature}
```
C++ fuzzing harness:
"""

COMPILE_PROMPT_TEMPLATE = """You are a c/c++ compiling expert.  
    The existing file name of fuzzing driver is `{harness_path}`.
    Do not use other C code or CPP code starting with `$SRC` from the build script.
    Please find the build command for compiling a single fuzzing driver for target project from the build script below.
    (Note: please replace $CC with clang and $CXX with clang++; remove environment variables at the CMD like `$CFLAGS` and `$CXXFLAGS` ;
    show the code as output only): 
    ```
        {code}
    ```
    Show me the compile command only, no other text.
    """

LINK_PROMPT_TEMPLATE = """You are a c/c++ compiling expert.  
    The existing file name of fuzzing driver is `{harness_path}`.
    Please find the build command for linking a single fuzzing driver with Libfuzzer for target project from the build script below so that we can start running the fuzzer.

    (Note: please replace $CC with clang and $CXX with clang++; remove environment variables at the CMD like `$CFLAGS` and `$CXXFLAGS`;
     the name of output binary is `./fuzz_harness` ; add essential linking flags for Libfuzzer; show the code as output only): 
    ```
        {code}
    ```

    The compile-only command is showed as below
    ```
        {compile_line}
    ```

    Show me the command for linkage only, no other text.
    """