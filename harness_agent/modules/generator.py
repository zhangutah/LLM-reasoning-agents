from langchain_core.language_models import BaseChatModel
from langchain_core.messages import BaseMessage
import logging
from typing import Callable, Any
from utils.misc import save_code_to_file
from pathlib import Path
from utils.misc import fix_qwen_tool_calls, fix_claude_tool_calls
from langgraph.graph import END # type: ignore

class HarnessGenerator:
    def __init__(self, runnable: BaseChatModel, max_tool_call: int, continue_flag: bool, 
                 save_dir: Path, code_callback: Callable[[str], str], logger: logging.Logger, model_name: str = ""):

        self.runnable = runnable
        self.save_dir = save_dir
        self.code_callback = code_callback
        self.logger = logger
        self.tool_history = []
        self.max_tool_call = max_tool_call
        self.continue_flag = continue_flag
        self.count_tool_call = 0 # count the number of tool calls
        self.model_name = model_name

    def respond(self, state: dict[str, Any]) -> dict[str, Any]:
        # prompt is in the messages
        response = None # type: ignore
        for _ in range(3):
            response: BaseMessage = self.runnable.invoke(state["messages"])
        
            if hasattr(response, 'invalid_tool_calls') and response.invalid_tool_calls: # type: ignore
                # Choose the appropriate fix function based on model type
                if self.model_name.startswith("anthropic"):
                    response = fix_claude_tool_calls(response)  # type: ignore
                else:
                    response = fix_qwen_tool_calls(response)  # type: ignore
            if response:
                break
        if not response:
            self.logger.info(f"wrong tool call, stop generating.")
            self.logger.info(response)
            return {"messages": f"{END}. wrong tool call, stop generating."}
       
        # check if call the tool
        if len(response.tool_calls) != 0: # type: ignore
            self.count_tool_call += len(response.tool_calls) # type: ignore
            
            # check if the tool call is too much
            if self.count_tool_call > self.max_tool_call:
                return {"messages": f"{END}. Initial Generator exceeds max tool call {self.max_tool_call}"}
            else:
                return {"messages": response}

        # not call the tool, the response is the code. format the code
        source_code = self.code_callback(response.content) # type: ignore

        if source_code.strip() == "":
            self.logger.info(f"Empty code returned, stop generating.")
            return {"messages": f"{END}. Empty code returned, stop generating."}

        # add prompt to the messages
        if self.continue_flag:
            full_source_code = state["messages"][0].content + source_code
        else:
            full_source_code = source_code
        if full_source_code.strip() == "":
            self.logger.info(f"Empty code returned, stop generating.")
            self.logger.info(response)
            return {"messages": f"{END}. Empty code returned, stop generating."}
        # save source code to file
        save_code_to_file(full_source_code,  self.save_dir / "draft_fix0.txt")

        self.logger.info(f"Generate Draft Code.")
        return {"messages": ("assistant", source_code), "harness_code": full_source_code, "fix_counter": 0}

