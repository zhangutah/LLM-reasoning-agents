from urllib import response
from multilspy import LanguageServer
from multilspy.multilspy_config import MultilspyConfig
from multilspy.multilspy_logger import MultilspyLogger
from constants import LanguageType, LSPFunction
import subprocess as sp
from typing import Any
import urllib
from agent_tools.code_tools.base_lsp_code_retriever import BaseLSPCodeRetriever
from pathlib import Path

class MultiLSPCodeRetriever(BaseLSPCodeRetriever):
    def __init__(self, workdir: str, project_name: str, project_lang: LanguageType, symbol_name: str, lsp_function: LSPFunction):
        super().__init__(workdir, project_name, project_lang, symbol_name, lsp_function)

    
    def fectch_code_from_response(self, response: list[dict[str, Any]], lsp_function: LSPFunction) -> list[dict[str, Any]]:
        return []
       

    async def request_function(self, file_path: str, start_line: int, end_line: int, charpos: int) -> list[dict[str, Any]]:
        """
        Args:
            file_path (str): The C++ source file including the symbol.
            line_start (int): The starting line number where the symbol is located.
            line_end (int): The ending line number where the symbol is located.
            charpos (int): The character position within the line where the symbol is located.
        Returns:
            list[dict]: The list of source code and corresponding file.
        Raises:
            Exception: If there is an error during the request to the LSP server.
        """
        response = []

        if self.lsp_function in [LSPFunction.Declaration, LSPFunction.Definition]:
          
            # For java, declaration and definition are the same
            # just return the line between line_start and line_end
            source_code = "\n".join(Path(file_path).read_text(encoding="utf-8").splitlines()[start_line:end_line+1])
            return [{"source_code": source_code, "file_path": file_path, "type": "", "start_line": start_line}]
        
        elif self.lsp_function == LSPFunction.References:
            response = await self.lsp_client.request_references(file_path, lineno=start_line, charpos=charpos)
            return self.fectch_code_from_response(response, self.lsp_function)
        else:
            return []

    async def get_all_functions(self) -> tuple[str, list[dict[str, Any]]]:
        return "Not implemented", []
        

async def get_multi_response(workdir: str, project_name: str, lang: str, symbol_name: str, lsp_function: str) -> tuple[str, list[dict[str, Any]]]:
        # the default workdir is the current directory, since we didn't send the compile_comamnd.json to the clangd server
    lsp = MultiLSPCodeRetriever(workdir, project_name, LanguageType(lang), symbol_name, LSPFunction(lsp_function))

    if lsp_function == LSPFunction.AllSymbols.value:
        msg, res = await lsp.get_all_functions()
    else:
        msg, res = await lsp.get_symbol_info()

    return msg, res