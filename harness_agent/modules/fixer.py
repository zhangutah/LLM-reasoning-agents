from langchain_core.language_models import BaseChatModel
from langchain_core.messages import BaseMessage
import logging
from typing import Callable, Any
from utils.misc import save_code_to_file
from pathlib import Path
from utils.misc import fix_qwen_tool_calls, fix_claude_tool_calls
from langgraph.graph import END # type: ignore

class CodeFixer:
    def __init__(self, runnable: BaseChatModel, max_fix: int, max_tool_call: int, save_dir: Path, 
                    cache_dir: Path, code_callback:Callable[[str], str] , logger:logging.Logger, model_name: str = ""):

        self.runnable = runnable
        self.save_dir = save_dir
        self.cache_dir = cache_dir

        self.code_callback = code_callback
        self.logger = logger
        self.max_tool_call = max_tool_call
        self.model_name = model_name
        self.max_fix = max_fix
        self.tool_call_counter = 0

    def respond(self, state: dict[str, Any]) -> dict[str, Any]:
        fix_counter = state.get("fix_counter", 0)
        self.logger.info(f"Fix start for draft_fix{fix_counter}.")
        
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
            self.tool_call_counter += len(response.tool_calls)  # type: ignore

            if self.tool_call_counter > self.max_tool_call:
                self.logger.info(f"Max tool call times ({self.max_tool_call}) reached.")
                return {"messages": f"{END}. Max tool call times ({self.max_tool_call}) reached."}

            # tool call response
            return {"messages": response}
       
        # not call the tool, the response is the code. 
        self.logger.info(f"Fix end for draft_fix{fix_counter}.")

        fix_counter = fix_counter+1
        if fix_counter > self.max_fix:
            self.logger.info(f"Max fix time ({self.max_fix}) reached")
            return {"messages": f"{END}. Max fix times ({self.max_fix}) reached", "fix_counter": fix_counter}
        
        # extract the code
        source_code = self.code_callback(response.content) # type: ignore
        if source_code.strip() == "":
            self.logger.info(f"Empty code returned for draft_fix{fix_counter}, stop fixing.")
            self.logger.info(response)
            return {"messages": f"{END}. Empty code returned, stop fixing.", "fix_counter": fix_counter}
        new_save_name = "draft_fix{}.txt".format(fix_counter)
        save_code_to_file(source_code, self.save_dir / new_save_name)
        # update the harness code
        return {"messages": ("assistant", source_code), "harness_code": source_code, "fix_counter": fix_counter}

