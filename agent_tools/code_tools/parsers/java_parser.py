from constants import LanguageType, LSPFunction
from agent_tools.code_tools.parsers.base_parser import BaseParser
from pathlib import Path
from typing import Optional
from tree_sitter import Node

decl_query_dict = {
    "functions": """
        (method_declaration
            name: (identifier) @identifier_name
            parameters: (formal_parameters) @function.params
            body: (block) @function.body
            (#eq? @identifier_name "{}")) @node_name""",
    "constructors": """
        (constructor_declaration
            (identifier) @identifier_name
            (formal_parameters) @constructor.params
            (#eq? @identifier_name "{}")) @node_name""",
    "fields": """
        (field_declaration
            (variable_declarator
                (identifier) @identifier_name)
            (#eq? @identifier_name "{}")) @node_name""",
    "classes": """
        (class_declaration
            name: (identifier) @identifier_name
            body: (class_body) @class.body
            (#eq? @identifier_name "{}")) @node_name
    """,
    "interfaces": """
        (interface_declaration
            name: (identifier) @identifier_name
           (#eq? @identifier_name "{}")) @node_name
    """,
    "enums": """
        (enum_declaration
            name: (identifier) @identifier_name
            (#eq? @identifier_name "{}")) @node_name
    """,
    "records": """
        (record_declaration
            name: (identifier) @identifier_name
            (#eq? @identifier_name "{}")) @node_name
    """,
    "annotations": """
        (annotation_type_declaration
            name: (identifier) @identifier_name
            (#eq? @identifier_name "{}")) @node_name
    """,
}


func_declaration_query_dict = {
    "functions": """
        (method_declaration
            name: (identifier) @identifier_name
            parameters: (formal_parameters) @function.params
            ) @node_name

        (constructor_declaration
            name: (identifier) @identifier_name
            parameters: (formal_parameters) @constructor.params
            ) @node_name
            """,
}
class JavaParser(BaseParser):
    def __init__(self, file_path: Optional[Path], source_code: Optional[str] = None):
        super().__init__(file_path, source_code, decl_query_dict=decl_query_dict, def_query_dict=decl_query_dict, func_declaration_query_dict=func_declaration_query_dict, project_lang=LanguageType.JAVA)
    
    
    # for java, the identifier is only one level deep 
    def get_identifier_node(self, root_node:Node, symbol_name: str) -> Optional[Node]:
        for child in root_node.children:
            if child.type != "identifier":
                continue
            if child.text and child.text.decode("utf-8", errors="ignore") == symbol_name:
                return child    
        return None
    
    def get_definition_node(self, function_name: str) -> Optional[Node]:
        # TODO this only test on C/C++ language
        
        # Define a query to find "function_definition" nodes
        function_definition_query = self.parser_language.query(f"({self.func_def_name}) @func_def")

        # Execute the query
        captures = function_definition_query.captures(self.tree.root_node)
        if not captures:
            return None
        # Check the nodes
        for node in captures["func_def"]:
            try:
                id_node = self.get_identifier_node(node, function_name)
                if id_node:
                    return node
            except Exception as e:
                print("Error in parsing the function definition: ", e)
        
        return None

# Example usage
if __name__ == "__main__":
    file_path = "/home/yk/code/LLM-reasoning-agents/test/demo.java"  # Replace with your C/C++ file path
    line = 46  # Replace with the line number of the function's start position
    column = 2  # Replace with the column number of the function's start position

    extractor = JavaParser(Path(file_path))
    # function_code = extractor.is_called("add")
    # is_called = extractor.have_definition("add")
    test_symbols = {
        "genericField":14,
        "Demo":17,
        "LocalHelper":19,
        "genericMethod":38,
        "NestedClass":46,
        "MyException":51,
        "Color": 56,
        "MyInterface": 61,
        "AbstractBase": 66,
        "Point": 72,
        "MyAnnotation": 78,
        "UsesEnum": 85,
        "annotatedMethod": 82,
    }
    for symbol, line in test_symbols.items():
        extracted_code = extractor.get_symbol_source(symbol, line, LSPFunction.Declaration)
        print(f"Function source code for {symbol}:")
        print(extracted_code)
        print("-----------------------------------------------------")
        assert extracted_code[1] != "", f"Failed to extract source code for {symbol}"
    
    file_path = "/home/yk/code/LLM-reasoning-agents/test/ExampleFuzzer.java"
    extractor = JavaParser(Path(file_path))
    if extractor.exist_function_definition("parse"):
        print("Fuzz function is faked.")
    else:
        print("Fuzz function is NOT Faked.")

    if extractor.is_fuzz_function_called("parse"):
        print("Fuzz function is called.")
    else:
        print("Fuzz function is NOT called.")


    res = extractor.get_ref_source("parse", 18)
    print(res)

    res = extractor.get_fuzz_function_node("parse", expression_flag=True)
    # test_namespace_identifier_matching()
    print(res)