# Evaluating LLM Agents for Fuzzing

This project focuses on evaluating large language model (LLM) agents for fuzzing tasks. It provides a framework for testing and comparing the effectiveness of various LLM-based agents in generating fuzzing harness/input/mutator.

## Directory Structure

- **agents**: Contains the code for multiple LLM agents. Each agent is designed to interact with fuzzing tools and generate test cases.
  
- **prompts**: Includes prompt template files used to guide the LLM agents. These templates help in structuring the input for the models to produce relevant outputs.
  
- **tools**: Provides utility functions that the agents can use. These functions support tasks such as input validation, data transformation, and result analysis.

## Getting Started

### Prerequisites

- Python 3.8 or higher
- Required Python packages (listed in `requirements.txt`)

### Installation

1. Clone the repository:
   ```bash
   git clone https://github.com/yourusername/llm-fuzzing-evaluation.git
   cd llm-fuzzing-evaluation
   ```

2. Install the necessary packages:
   ```bash
   pip install -r requirements.txt
   ```

### Usage

1. **Configure Prompts**: Customize the prompt templates in the `prompts` directory to suit your fuzzing requirements.

2. **Run Agents**: Execute the agents from the `agents` directory. Each agent can be run independently to evaluate its performance.

   Example:
   ```bash
   python agents/plan_harness_gen.py
   ```

3. **Tools**: execution tools for LLM agents , or direct use with main function.


## License

This project is licensed under the MIT License. See the [LICENSE](LICENSE) file for details.

## Contact

For questions or suggestions, please open an issue.