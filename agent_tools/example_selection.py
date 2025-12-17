from langchain_openai import ChatOpenAI
from constants import PROJECT_PATH
from pathlib import Path
from pydantic import BaseModel, Field
import json
from typing import Any, Union
import tiktoken
import os

class AnswerStruct(BaseModel):
    """Split the response into the answer and the explanation."""
    answer: Union[str, bool] = Field(description="The answer to the question. Can be 'true'/'false' or a boolean.")
    explaination: str = Field(description="The explanation for the answer.")


class LLMSelector:
    def __init__(self, model_name: str):
        self.name = "LLM"
        if model_name.startswith("gpt"):
            llm = ChatOpenAI(model=model_name)
        else:
            llm = ChatOpenAI(
                api_key=os.getenv("OPENROUTER_API_KEY", ""), # type: ignore
                base_url="https://openrouter.ai/api/v1",
                model=model_name,
                temperature=0.)

        self.llm = llm
        self.structured_llm = llm.with_structured_output(AnswerStruct) # type: ignore
        prompt_path = Path(f"{PROJECT_PATH}/agent/prompts/example_selection.txt")
        self.prompt_template = prompt_path.read_text()


    def score_example(self, target_function: str, code: str) -> int:
        prompt = self.prompt_template.replace("{target_function}", target_function).replace("{code}", code)

        try:
            resp = self.structured_llm.invoke(prompt) # type: ignore
       
            # Handle both string and boolean responses
            if isinstance(resp.answer, bool): # type: ignore
                return 1 if resp.answer else 0 # type: ignore
            elif isinstance(resp.answer, str) and resp.answer.lower() == "true": # type: ignore
                return 1
            else:
                return 0

        except Exception as e:
            # try with no structured output
            print(f"Error invoking LLM: {e}")
            resp = self.llm.invoke(prompt)

            # Extract the answer from the raw text response
            answer_text = resp.content.strip().lower() # type: ignore
            if "true" in answer_text:
                return 1
            else:
                return 0
        

def cache_example_selection(json_file: Path, function_name:str, project_name:str,   llm_name: str = "gpt-4.1") -> list[dict[str, Any]]:
    """Cache the example selection results."""
    # 
    llm_norm = llm_name.replace("/", "_")
    save_json_file = json_file.with_name(json_file.name.replace(".json", f"_{llm_norm}.json"))
    if not json_file.exists():
        raise FileNotFoundError(f"File {json_file} does not exist.")

    if save_json_file.exists():
        # read json file 
        with open(save_json_file, 'r') as f:
            data = f.read()
            return json.loads(data)

    # Read the JSON file
    with open(json_file, 'r') as f:
        data = f.read()
        json_data = json.loads(data)
    # add new key-value pair to indicate the example 
    llm_selector = LLMSelector(llm_name)
    # a roughly 1000 tokens limit for the source code
    enc = tiktoken.encoding_for_model("gpt-4o")
    res_list:list[dict[str, Any]] = []            
    for example_json in json_data:
        
        source_code = example_json["source_code"]
        if len(enc.encode(source_code)) > 1000:
            res_list.append(example_json)
            continue

        example_json["selection_method"] = llm_selector.name
        example_json["selection_score"] = llm_selector.score_example(
            function_name,
            example_json["source_code"]
        )
        res_list.append(example_json)
    
    # Write the modified JSON data back to the file
    with open(save_json_file, 'w') as f:
        json.dump(res_list, f, indent=4)

    return res_list

if __name__ == "__main__":
    llm = LLMSelector("gpt-4-0613")
    code = """
int\nLLVMFuzzerTestOneInput(const uint8_t *data, size_t size) {\n    sip_msg_t orig_inv = { };\n    orig_inv.buf = (char*)data;\n    orig_inv.len = size;\n\n    if(size >= 4*BUF_SIZE) {\n        /* test with larger message than core accepts, but not indefinitely large */\n        return 0;\n    }\n\n    if (parse_msg(orig_inv.buf, orig_inv.len, &orig_inv) < 0) {\n        goto cleanup;\n    }\n\n    parse_headers(&orig_inv, HDR_EOH_F, 0);\n\n    parse_sdp(&orig_inv);\n\n    parse_from_header(&orig_inv);\n\n    parse_from_uri(&orig_inv);\n\n    parse_to_header(&orig_inv);\n\n    parse_to_uri(&orig_inv);\n\n    parse_contact_headers(&orig_inv);\n\n    parse_refer_to_header(&orig_inv);\n\n    parse_pai_header(&orig_inv);\n\n    parse_diversion_header(&orig_inv);\n\n    parse_privacy(&orig_inv);\n\n    parse_content_disposition(&orig_inv);\n\n    parse_identityinfo_header(&orig_inv);\n\n    parse_record_route_headers(&orig_inv);\n\n    parse_route_headers(&orig_inv);\n\n    str uri;\n    get_src_uri(&orig_inv, 0, &uri);\n\n    str ssock;\n    get_src_address_socket(&orig_inv, &ssock);\n\ncleanup:\n    free_sip_msg(&orig_inv);\n\n    return 0;\n}
    """

    target_function = """
    int parse_from_header(struct sip_msg *msg)
    """

    print(llm.score_example(target_function, code))