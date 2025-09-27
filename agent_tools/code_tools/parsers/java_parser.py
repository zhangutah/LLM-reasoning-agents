from constants import LanguageType, LSPFunction
from agent_tools.code_tools.parsers.base_parser import BaseParser
from pathlib import Path
from typing import Optional


class JavaParser(BaseParser):
    def __init__(self, file_path: Optional[Path], source_code: Optional[str] = None):
        super().__init__(file_path, source_code, decl_query_dict={}, def_query_dict={}, func_declaration_query_dict={}, project_lang=LanguageType.JAVA)

    def get_symbol_source(self, symbol_name: str, line: int, lsp_function: LSPFunction) -> tuple[str, str, int]:
        """
        Retrieve the full source code of a symbol based on its start position.
        :param symbol_name: The name of the function to find.
        :param line: The line number of the function's start position (0-based).
        :param column: The column number of the function's start position (0-based).
        :return: The full source code of the function.
        """
        # Define a query to find "definition" and "declaration" nodes
        method_declaration_query = self.parser_language.query("""(method_declaration) @func_decl""")    
        field_declaration_query = self.parser_language.query("""(field_declaration) @func_decl""")
        constructor_declaration_query = self.parser_language.query("""(constructor_declaration) @func_decl""")
        class_declaration_query = self.parser_language.query("""(class_declaration) @func_decl""")
        interface_declaration_query = self.parser_language.query("""(interface_declaration) @func_decl""")
     
        # type s
        if lsp_function in [LSPFunction.Declaration, LSPFunction.Definition]:
            query_list = [method_declaration_query, constructor_declaration_query, field_declaration_query, class_declaration_query, interface_declaration_query]
        else:
            print("Unsupported LSP function.")
            return "", "", 0
            
        for query in query_list:

            # Execute the query
            captures = query.captures(self.tree.root_node)

            if not captures:
                continue

            # Print the nodes
            for node in captures["func_decl"]:
                if not node.text:
                    continue

                # find the identifier node since it is the method name
                identifier_node = None
                for child_node in node.children:
                    if child_node.type != "identifier":
                        continue
                    if not child_node.text:
                        continue
                    identifier_node = child_node
                    break

                if not identifier_node or not identifier_node.text:
                    continue

                # make sure the identifier node is the symbol we are looking for
                # java may function call
                if identifier_node.text.decode("utf-8", errors="ignore") == symbol_name and node.start_point.row <= line and line <= node.end_point.row:
                    source_code = node.text.decode("utf-8", errors="ignore")
                    return "", source_code, node.start_point.row + 1  # return 1-based line number

        return "", "", 0


# Example usage
if __name__ == "__main__":
    file_path = "tools/code_tools/java/demo.java"  # Replace with your C/C++ file path
    line = 46  # Replace with the line number of the function's start position
    column = 2  # Replace with the column number of the function's start position

    extractor = JavaParser(Path(file_path))
    # function_code = extractor.is_called("add")
    # is_called = extractor.have_definition("add")
    extracted_code = extractor.get_symbol_source("getBoolean", 81, LSPFunction.Declaration)
    print("Function source code:")
    print(extracted_code)