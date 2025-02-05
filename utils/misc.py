from langchain_openai import ChatOpenAI
from langchain_anthropic import ChatAnthropic
from langchain_google_genai import ChatGoogleGenerativeAI
from langchain_core.language_models import BaseChatModel
from langchain import graphs
import os
from matplotlib import pyplot as plt
import io
import docker
import yaml
import subprocess as sp
from collections import defaultdict
from constants import PROJECT_PATH
from langgraph.graph import StateGraph
import re
import logging  

def save_code_to_file(code: str, file_path: str) -> None:
    '''Save the code to the file'''

    dirname = os.path.dirname(file_path)
    if not os.path.exists(dirname):
        os.makedirs(dirname)

    with open(file_path, "w") as f:
        f.write(code)


def plot_graph(graph: StateGraph, save_flag: bool = True) -> None:
    # Assuming graph.get_graph().draw_mermaid_png() returns a PNG image file path
    image_data = graph.get_graph().draw_mermaid_png()

    # Use matplotlib to read and display the image
    img = plt.imread(io.BytesIO(image_data))
    plt.axis('off')  # Hide axes

    if save_flag:
        plt.savefig("graph.png")
    else:
        plt.imshow(img)
        plt.show()



def remove_color_characters(text: str) -> str:
      # remove color characters
    ansi_escape = re.compile(r'\x1b\[[0-9;]*m')
    return ansi_escape.sub('', text)


def load_pormpt_template(template_path: str) -> str:
    '''Load the prompt template'''
    with open(template_path, 'r') as file:
        return file.read()



def load_model_by_name(model_name: str, temperature: float = 0.7) -> BaseChatModel:
    '''Load the model by name'''

    #  obtain environment variables
    DEEPSEEK_API_KEY = os.getenv("DEEPSEEK_API_KEY")

    name_vendor_mapping = {
        "gpt-4o":"openai",
        "gpt-40-mini":"openai",
        "gpt-4o-turbo":"openai",
        "gemini-2.0-flash-exp":  "google",
        "gemini-1.5-flash": "google",
        "deepseekv3": "deepseek",
    }

    assert model_name in name_vendor_mapping.keys

    vendor_name = name_vendor_mapping.get(model_name)
    if vendor_name == "openai":
        return ChatOpenAI(model_name, temperature=temperature)
    elif vendor_name == "deepseek":
        assert DEEPSEEK_API_KEY is not None
        return ChatOpenAI(model_name='deepseek-chat', openai_api_key=DEEPSEEK_API_KEY, openai_api_base='https://api.deepseek.com')
    elif vendor_name == "anthropic":
        return ChatAnthropic(model_name, temperature=temperature)
    elif vendor_name == "google":
        return ChatGoogleGenerativeAI(model_name, temperature=temperature)
    else:
        return None
    

def function_statistics():

    # read benchmark names
    bench_dir = os.path.join(PROJECT_PATH, "benchmark-sets", "all")

    function_list = []
    for file in os.listdir(bench_dir):
        # read yaml file
        with open(os.path.join(bench_dir, file), 'r') as f:
            data = yaml.safe_load(f)
            project_name = data.get("project")
            lang_name = data.get("language")
            project_harness = data.get("target_path")

            if lang_name not in ["c++", "c"]:
                continue
        
            n_function = len(data.get("functions"))
            function_list.append(n_function)
    print(f"Total number of projects: {len(function_list)}")
   
    total_func = 0
    for i in range(1, 6):
        print(f"{i} of functions in {i} projects: {function_list.count(i)}")
        total_func += i * function_list.count(i)
  
    print(f"Total number of functions: {total_func}")



def project_statistics():

    # read benchmark names
    bench_dir = os.path.join(PROJECT_PATH, "benchmark-sets", "all")

    all_projects = []
    for file in os.listdir(bench_dir):
        # read yaml file
        with open(os.path.join(bench_dir, file), 'r') as f:
            data = yaml.safe_load(f)
            project_name = data.get("project")
            lang_name = data.get("language")
            project_harness = data.get("target_path")

            all_projects.append((project_name, lang_name, project_harness))


    # open another file
    build_res_file = os.path.join(PROJECT_PATH, "prompts", "res.txt")

    build_res = {} 
    with open(build_res_file, 'r') as f:
        for line in f:
            project_name, res = line.split(";")
            build_res[project_name] = res

    lang_count = defaultdict(int)

    for project_name, lang_name, project_harness in all_projects:

        if "Error" in build_res[project_name]:
            print(f"{project_name} {build_res[project_name]}")
            # remove from benchmark 
            # file_path = os.path.join(bench_dir, f"{project_name}.yaml")
            # os.remove(file_path)

            continue

        lang_count[lang_name] += 1

    print(lang_count)


if __name__ == "__main__":
    function_statistics()