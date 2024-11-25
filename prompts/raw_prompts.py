FUNCTION_ANALYSIS_PROMPT = """
Analyze the given function signature and list each parameter with its data type and any initialization requirements.
Function signature:
```
{function_signature}
```
Your analysis should include the purpose of each parameter if known, and how it should be initialized for a valid test.
"""

fuzzing_harness_generation_prompt = """
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