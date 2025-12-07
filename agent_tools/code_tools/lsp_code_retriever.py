import json
import os
import argparse
import asyncio
from constants import LanguageType, LSPFunction
from agent_tools.code_tools.cpp_lsp_code_retriever import get_cpp_response
from agent_tools.code_tools.multi_lsp_code_retriever import get_multi_response

async def main():
    parser = argparse.ArgumentParser(description='')
    parser.add_argument('--project', type=str, default="cppcheck", help='The project name.')
    parser.add_argument('--workdir', type=str, default="/src/cppcheck", help='The work place that can run bear compile.')
    parser.add_argument('--lsp-function', type=str, default="declaration", choices=[e.value for e in LSPFunction], help='The LSP function name')
    parser.add_argument('--symbol-name', type=str, default="CppCheck::check", help='The function name or struct name.')
    parser.add_argument('--lang', type=str, default="CPP", choices=[e.value for e in LanguageType], help='The project language.')
    args = parser.parse_args()

    if args.lang in [LanguageType.CPP.value, LanguageType.C.value]:
        msg, res = await get_cpp_response(args.workdir, args.project, args.lang, args.symbol_name, args.lsp_function)
    else:
        msg, res = await get_multi_response(args.workdir, args.project, args.lang, args.symbol_name, args.lsp_function)
    file_name = f"{args.symbol_name}_{args.lsp_function}_lsp.json"
    with open(os.path.join("/out", file_name), "w") as f:
        f.write(json.dumps({"message": msg, "response": res}, indent=4))

if __name__ == "__main__":
    asyncio.run(main())
