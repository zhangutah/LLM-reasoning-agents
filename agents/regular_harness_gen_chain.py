from langchain.prompts import PromptTemplate
from langchain_openai import ChatOpenAI
from langchain.chains import LLMChain
from langsmith.wrappers import wrap_openai
# import openai

# Set up prompt templates
function_analysis_prompt = """
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

# Initialize OpenAI model
# client = wrap_openai(openai.Client())
llm = ChatOpenAI(model="gpt-4o")

# Define the chain for function analysis
function_analysis_chain = LLMChain(
    llm=llm,
    prompt=PromptTemplate(
        input_variables=["function_signature"],
        template=function_analysis_prompt,
    )
)

# Define the chain for fuzzing harness generation
fuzzing_harness_generation_chain = LLMChain(
    llm=llm,
    prompt=PromptTemplate(
        input_variables=["function_analysis", "guidelines", "function_signature"],
        template=fuzzing_harness_generation_prompt,
    )
)

# Function for generating the fuzzing harness
def generate_fuzzing_harness(function_signature: str, guidelines: str) -> str:
    # Step 1: Analyze function signature
    function_analysis = function_analysis_chain.run({"function_signature": function_signature})

    # Step 2: Generate fuzzing harness
    fuzzing_harness = fuzzing_harness_generation_chain.run({
        "function_analysis": function_analysis,
        "guidelines": guidelines,
        "function_signature": function_signature
    })

    return fuzzing_harness

# Example usage
if __name__ == "__main__":
    # Define the function signature and guidelines for fuzzing
    function_signature = "int xmlTextReaderSetSchema(xmlTextReaderPtr reader, xmlSchemaPtr schema)"
    guidelines = """
    - Carefully study the function signature and initialize all parameters.
    - Use FuzzedDataProvider for generating various input data types.
    - Ensure the code compiles successfully, including any required header files.
    - All variables must be declared and initialized before usage.
    - Avoid creating new variables with names identical to existing ones.
    - Add type casts where necessary to ensure type matching.
    - Avoid using random number generators such as rand().
    - If using `goto`, declare variables before the `goto` label.
    - You must call `int xmlTextReaderSetSchema(xmlTextReaderPtr reader, xmlSchemaPtr schema)` in the solution.
    """

    # Generate the fuzzing harness
    fuzzing_harness_code = generate_fuzzing_harness(function_signature, guidelines)
    print("Generated Fuzzing Harness:\n")
    print(fuzzing_harness_code)