# LLM Reasoning Agents for Fuzzing

A comprehensive framework for evaluating and deploying Large Language Model (LLM) agents in automated fuzzing tasks. This project leverages state-of-the-art LLMs to automatically generate fuzz harnesses, analyze codebases, and fix compilation errors in fuzzing campaigns targeting C/C++ and Java projects.

## Overview

This framework implements LLM-powered agents that can:
- **Automatically generate fuzz harnesses** for target functions in OSS-Fuzz projects
- **Retrieve and analyze code context** using LSP (Language Server Protocol) and cscope
- **Fix compilation errors** iteratively using agent-based reasoning
- **Select relevant examples** from existing fuzz targets to guide generation
- **Validate and build** generated fuzz targets in Docker environments

The system supports multiple LLM backends (OpenAI GPT, Claude) and provides configurable pipelines for different fuzzing workflows.

## Features

- **LLM-Powered Agent**: Single reasoning agent with specialized modules for code generation, error fixing, and validation
- **Advanced Code Retrieval**: LSP and cscope integration for semantic code understanding
- **Automated Error Fixing**: Iterative compilation error resolution with configurable strategies (raw, ISSTA, OSS-Fuzz, agent)
- **Benchmark Support**: Built-in benchmark sets with 200+ OSS-Fuzz projects for systematic evaluation
- **Docker Integration**: Seamless OSS-Fuzz environment containerization
- **Example-Driven Generation**: Intelligent selection of reference fuzz targets
- **State Management**: LangGraph-based workflow with checkpointing and memory

## Architecture

### Core Components

```
LLM-reasoning-agents/
├── agent/                      # Main agent implementation
│   ├── gen.py                  # Core fuzzer generation logic
│   ├── run_gen.py              # Entry point for harness generation
│   ├── eval.py                 # Harness evaluation and coverage collection
│   ├── modules/                # Core modules
│   │   ├── generator.py        # Harness code generator
│   │   ├── fixer.py            # Compilation error fixer
│   │   ├── validation.py       # Harness validation
│   │   ├── compilation.py      # Compilation handling
│   │   ├── fuzzenv.py          # Fuzzing environment setup
│   │   ├── semantic_check.py   # Semantic validation
│   │   └── code_format.py      # Code formatting utilities
│   ├── fixing/                 # Error fixing strategies
│   │   ├── raw.py              # Basic error fixing
│   │   ├── issta.py            # ISSTA-style fixing with code context
│   │   └── oss_fuzz.py         # OSS-Fuzz-Gen fixing strategy
│   ├── header/                 # Header file analysis
│   └── prompts/                # Prompt templates for LLM agents
│
├── agent_tools/                # Tool implementations for agents
│   ├── code_search.py          # Public usage search (GitHub, Sourcegraph)
│   ├── code_retriever.py       # Code context retrieval
│   ├── example_selection.py    # Example-based learning
│   ├── results_analysis.py     # Results aggregation and analysis
│   ├── code_tools/             # Code analysis tools
│   │   ├── lsp_code_retriever.py       # LSP-based code retrieval
│   │   ├── cpp_lsp_code_retriever.py   # C/C++ specific LSP
│   │   ├── parser_code_retriever.py    # Parser-based retrieval
│   │   ├── multi_lsp_code_retriever.py # Multi-LSP support
│   │   ├── lsp_clients/        # LSP client implementations
│   │   └── parsers/            # Language parsers
│   └── fuzz_tools/             # Fuzzing utilities
│
├── ossfuzz_gen/                # OSS-Fuzz-Gen integration
├── benchmark-sets/             # Evaluation benchmarks (see below)
├── cfg/                        # Configuration files (see below)
├── cache/                      # Project caches for LSP
├── utils/                      # Utility functions
├── bench_cfg.py                # Configuration class definition
└── constants.py                # Global constants and enums
```

## Installation

### Prerequisites

- Python 3.10 or higher
- Docker (for OSS-Fuzz integration)
- OSS-Fuzz repository (for benchmark projects)

### Setup

1. **Clone the repository:**
   ```bash
   git clone https://github.com/zhangutah/LLM-reasoning-agents.git
   cd LLM-reasoning-agents
   ```

2. **Install dependencies:**
   First create a new conda env with python=3.10.
   ```bash
   pip install multilspy
   pip install -r requirements.txt
   ```

3. **Configure environment variables:**
   Create a `.env` file with your API keys:
   ```bash
   OPENAI_API_KEY=your_openai_api_key
   ```

4. **Set up OSS-Fuzz:**
   ```bash
   git clone https://github.com/google/oss-fuzz.git
   # Update oss_fuzz_dir in configuration files
   ```
   
   **Important**: OSS-Fuzz recently updated the base docker image to Clang 21, but libclang (used by clangd) currently only supports version 18. To ensure compatibility, pin the Docker base image to a previous version by modifying the Dockerfile in your target project directory:
   
   ```dockerfile
   FROM gcr.io/oss-fuzz-base/base-builder@sha256:d34b94e3cf868e49d2928c76ddba41fd4154907a1a381b3a263fafffb7c3dce0
   ```

## Configuration

The framework uses YAML configuration files in the `cfg/` directory. The configuration is parsed by `BenchConfig` class in `bench_cfg.py`.

### Configuration File Structure

Below is a complete example configuration with all available options:

```yaml
# ==================== Path Settings ====================
oss_fuzz_dir: /path/to/oss-fuzz/          # Path to OSS-Fuzz repository
save_root: outputs/experiment_name/        # Directory to save results (relative or absolute)
cache_root: /path/to/cache/                # Cache directory for project LSP data
bench_dir: benchmark-sets/all              # Benchmark set directory

# ==================== Model Settings ====================
model_name: "gpt-4o"                        # LLM model name
reasoning: false                            # Enable reasoning mode (for o1-style models)
temperature: 0.7                            # Sampling temperature (0.0-2.0)
model_token_limit: 8096                     # Maximum tokens for model context
usage_token_limit: 1000                     # Token limit for usage examples

# ==================== Experiment Settings ====================
run_time: 1                                 # Fuzzing run time in minutes
max_fix: 5                                  # Maximum fix iterations per harness
max_tool_call: 15                           # Maximum tool calls per agent run
iterations: 3                               # Number of generation iterations
num_processes: 8                            # Parallel processes (default: cpu_count/3)

# ==================== Target Selection ====================
project_name: []                            # List of projects to process (empty = all)
function_signatures: []                     # Specific functions to target (empty = all)
funcs_per_project: 1                        # Number of functions per project

# ==================== Language Settings ====================
language: "CPP"                             # Target language: CPP, C, JVM

# ==================== Example Settings ====================
n_examples: 1                               # Number of examples to retrieve
example_mode: "rank"                        # Example selection: "rank" or "random"
example_source: "project"                   # Source: "project" (same project examples)

# ==================== Agent Behavior Settings ====================
fixing_mode: "agent"                        # Error fixing strategy (see below)
header_mode: "agent"                        # Header resolution mode (see below)
memory_flag: false                          # Enable agent memory across iterations
clear_msg_flag: true                        # Clear messages between fix attempts
definition_flag: false                      # Include function definitions in context
driver_flag: false                          # Include driver code context
compile_enhance: false                      # Enable compilation enhancement

# ==================== Validation Settings ====================
semantic_mode: "both"                       # Semantic check: "both", "eval", "none"
use_cache_harness_pairs: true               # Use cached successful harness pairs

# ==================== Fuzzing Settings ====================
no_log: false                               # Disable fuzzing logs
ignore_crashes: false                       # Continue fuzzing after crashes
```

### Configuration Options Explained

**Fixing Modes** (`fixing_mode`):
- `raw`: Basic error fixing - sends error message directly to LLM
- `issta`: ISSTA-style fixing - augments error with function declarations and usage examples
- `oss_fuzz`: OSS-Fuzz-Gen fixing - uses OSS-Fuzz-Gen's context collection and instruction generation
- `agent`: Agent-based fixing - uses tool-calling agent for error resolution

**Header Modes** (`header_mode`):
- `static`: Use predefined header files
- `all`: Include all project headers
- `agent`: Agent determines required headers dynamically
- `oss_fuzz`: Use OSS-Fuzz-Gen's header detection
- `no`: No additional header handling

**Language Types** (`language`):
- `CPP`: C++ projects (also works for C)
- `C`: C projects
- `JVM`: Java projects

## Benchmark Sets

Benchmark sets are located in the `benchmark-sets/` directory. Each benchmark defines target functions for fuzz harness generation.

### Directory Structure

```
benchmark-sets/
├── function_0/             # Partition 0 (same as all/)
├── projects/               # Individual project benchmarks
```

### Benchmark File Format (YAML)

Each project has a YAML file defining target functions:

**C/C++ Example** (`libxml2.yaml`):
```yaml
functions:
- name: xmlXIncludeProcessTreeFlags
  params:
  - name: tree
    type: 'bool '
  - name: flags
    type: int
  return_type: int
  signature: int xmlXIncludeProcessTreeFlags(xmlNodePtr, int)
- name: htmlSAXParseFile
  params:
  - name: filename
    type: 'bool '
  - name: encoding
    type: 'bool '
  - name: sax
    type: 'bool '
  - name: userData
    type: 'bool '
  return_type: void
  signature: htmlDocPtr htmlSAXParseFile(const char *, const char *, htmlSAXHandlerPtr, void *)
language: c++
project: libxml2
target_name: regexp                         # Reference fuzzer name
target_path: /src/libxml2/fuzz/regexp.c     # Reference fuzzer path
```

**Java Example** (`jsoup.yaml`):
```yaml
functions:
- name: "[org.jsoup.select.NodeTraversor].traverse(org.jsoup.select.NodeVisitor,org.jsoup.nodes.Node)"
  params:
  - name: arg0
    type: org.jsoup.select.NodeVisitor
  - name: arg1
    type: org.jsoup.nodes.Node
  return_type: void
  signature: "[org.jsoup.select.NodeTraversor].traverse(org.jsoup.select.NodeVisitor,org.jsoup.nodes.Node)"
language: jvm
project: jsoup
target_name: XmlFuzzer
target_path: /src/XmlFuzzer.java
```

### Benchmark Field Descriptions
The only used filed is 'signature' for agent mode, so you can randomly fill other fields.

| Field | Description |
|-------|-------------|
| `functions` | List of target functions to generate harnesses for |
| `functions[].name` | Function name (for Java: `[ClassName].methodName(params)`) |
| `functions[].signature` | Full function signature (required for generation) |
| `functions[].params` | Parameter list with names and types |
| `functions[].return_type` | Function return type |
| `language` | Project language: `c`, `c++`, or `jvm` |
| `project` | OSS-Fuzz project name |
| `target_name` | Reference fuzzer name in the project |
| `target_path` | Path to reference fuzzer in Docker container |


### Available Benchmark Sets

| Benchmark | Projects | Language | Description |
|-----------|----------|----------|-------------|
| `function_0/` | ~200 each | C/C++ | Partitioned benchmarks for distributed runs |
| `projects/` | cjson | C/C++ |  |

## Usage

### Harness Generation

1. **Prepare a benchmark set** (required):
   - Create a YAML file for your target project in `benchmark-sets/` directory
   - Define target functions with their signatures (see Benchmark File Format above)
   - The `signature` field is required; other fields can be placeholder values
   
   Example minimal benchmark (`benchmark-sets/myproject/mylib.yaml`):
   ```yaml
   functions:
   - name: my_function
     signature: int my_function(const char *, int)
   language: c++
   project: mylib
   target_name: existing_fuzzer
   target_path: /src/mylib/fuzz/fuzzer.c
   ```

2. **Create a configuration file** in `cfg/` directory:
   - Set `bench_dir` to point to your benchmark set directory
   - Configure other options as needed (see Configuration section above)

3. **Update `run_gen.py`** to point to your config:
   ```python
   cfg_list = [
       "/path/to/your/config.yaml"
   ]
   ```

4. **Run the generation**:
   ```bash
   python -m agent.run_gen
   ```

The agent will:
- Load target functions from the specified benchmark set (`bench_dir`)
- For each function, generate a fuzz harness using the LLM
- Attempt to compile and fix errors iteratively
- Save results to the configured `save_root` directory

### Benchmark Evaluation

After generating harnesses, evaluate them with extended fuzzing and coverage collection:

```bash
python -m agent.eval
```

This will:
- Recompile successful harnesses
- Run extended fuzzing campaigns
- Collect coverage data for target functions
- Save corpus files for reproducibility

## Workflow

The agent operates through the following pipeline:

1. **Initialization**: Load target function and project context
2. **Example Selection**: Retrieve relevant example fuzz targets from the same project
3. **Code Retrieval**: Gather necessary headers and definitions using LSP/cscope
4. **Generation**: LLM generates initial fuzz harness
5. **Compilation**: Build harness in Docker environment
6. **Error Fixing**: Iteratively fix compilation errors (up to `max_fix` attempts)
7. **Validation**: Verify successful compilation and execution
8. **Evaluation**: Analyze results and save artifacts

## Output Structure

Results are saved in the following structure:

```
save_root/
├── project_name/
│   └── function_name/
│       └── run1/
│           ├── harness.txt          # Generated harness code
│           ├── agent.log            # Agent execution log
│           ├── fuzzer_info.json     # Fuzzer metadata
│           ├── include_path.txt     # Required include paths
│           └── corpora/             # Fuzzing corpus (after eval)
├── res_1.txt                        # Iteration 1 results summary
├── success_functions_1.json         # Successfully generated functions
└── config.yaml                      # Copy of configuration used
```

## Supported Languages

- **C/C++**: Full support with cscope and clangd LSP
- **Java**: Working with JVM-specific benchmarks

## Contributing

Contributions are welcome! Please feel free to submit issues or pull requests.

## License

This project is licensed under the MIT License. See the [LICENSE](LICENSE) file for details.

## Contact

For questions, suggestions, or issues, please open an issue on GitHub.