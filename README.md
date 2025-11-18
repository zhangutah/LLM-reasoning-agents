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

- 🤖 **LLM-Powered Agent**: Single reasoning agent with specialized modules for code generation, error fixing, and validation
- 🔍 **Advanced Code Retrieval**: LSP and cscope integration for semantic code understanding
- 🛠️ **Automated Error Fixing**: Iterative compilation error resolution with configurable strategies
- 📊 **Benchmark Support**: Built-in benchmark sets for systematic evaluation
- 🐳 **Docker Integration**: Seamless OSS-Fuzz environment containerization
- 🧪 **Example-Driven Generation**: Intelligent selection of reference fuzz targets
- 🔄 **State Management**: LangGraph-based workflow with checkpointing and memory

## Architecture

### Core Components

```
├── harness_agent/          # Main agent implementation
│   ├── modules/            # Core modules (generator, fixer, validator)
│   ├── fixing/             # Error fixing strategies (raw, ISSTA, OSS-Fuzz)
│   ├── header/             # Header file analysis and compilation
│   ├── prompts/            # Prompt templates for LLM agents
│   └── evaluation/         # Evaluation and metrics
│
├── agent_tools/            # Tool implementations for agents
│   ├── code_search.py      # Public usage search functionality
│   ├── code_retriever.py   # Code context retrieval
│   ├── example_selection.py # Example-based learning
│   └── code_tools/         # Additional code analysis tools
│
├── ossfuzz_gen/            # For OSS-Fuzz-Gen Fixing Strategy
│
├── benchmark-sets/         # Evaluation benchmarks
├── cfg/                    # Configuration files
├── utils/                  # Utility functions
└── src/run                 # the core file to run harness generation
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

## Usage


### Configuration

The framework uses YAML configuration files in the `cfg/` directory for generation and evaluation. See the example cfg

### Target function
The target function should follow the yaml format the same as the OSS-Fuzz-Gen, but the only necessary item is the target function signature.

### Harness Generation

Run the main agent pipeline:

```bash
python -m src.run
```

This will execute the default harness generation agent configured in `run.py`.


## Benchmark Evaluation

The will evalute the generate harness by runing more time and save the corpus for fine-grained target function coverage collection.

```bash
python -m harness_agent.evaluation.evaluation
```

## Workflow

The agent operates through the following pipeline:

1. **Initialization**: Load target function and project context
2. **Example Selection**: Retrieve relevant example fuzz targets
3. **Code Retrieval**: Gather necessary headers and definitions using LSP/cscope
4. **Generation**: LLM generates initial fuzz harness
5. **Compilation**: Build harness in Docker environment
6. **Error Fixing**: Iteratively fix compilation errors
7. **Validation**: Verify successful compilation and execution
8. **Evaluation**: Analyze results and save artifacts

## Supported Languages

- **C/C++**: Full support with cscope and clangd LSP
- **Java**: Support via Eclipse JDT.LS

## Contributing

Contributions are welcome! Please feel free to submit issues or pull requests.

## License

This project is licensed under the MIT License. See the [LICENSE](LICENSE) file for details.

## Citation

If you use this framework in your research, please cite:

```bibtex
@misc{llm-reasoning-agents,
  author = {Zhang, Utah},
  title = {LLM Reasoning Agents for Fuzzing},
  year = {2025},
  url = {https://github.com/zhangutah/LLM-reasoning-agents}
}
```

## Contact

For questions, suggestions, or issues, please open an issue on GitHub.