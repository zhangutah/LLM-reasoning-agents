from constants import LanguageType, LSPFunction
from agent_tools.code_tools.parsers.base_parser import FunctionDeclaration
from agent_tools.code_tools.parsers.c_parser import CParser
from agent_tools.code_tools.parsers.c_parser import common_query_dict, c_func_queries
from pathlib import Path
from typing import Optional
import re
# TODO no consider template yet

cpp_def_queries = {
    "classes": """
        (class_specifier
            name: (type_identifier) @identifier_name
             (#eq? @identifier_name "{}"))@node_name""",

    "template_fucntion": """
                    (template_declaration
                    (function_definition
                    [  (function_declarator
                                (identifier) @identifier_name)
                        (pointer_declarator
                                (function_declarator
                                    (identifier) @identifier_name))
                    ]        
                     (#eq? @identifier_name "{}"))) @node_name""",

    "fucntion": """(function_definition
                    [  (function_declarator
                                (identifier) @identifier_name)
                        (pointer_declarator
                                (function_declarator
                                    (identifier) @identifier_name))
                    ]        
                     (#eq? @identifier_name "{}")) @node_name""",
   

    "class_method": """(function_definition
                        (function_declarator
                        [(qualified_identifier
                            (identifier) @identifier_name)
                        (field_identifier) @identifier_name
                        ]
                         (#eq? @identifier_name "{}")))@node_name""",

        }

cpp_decl_queries = {
    "classes": """
        (class_specifier
            name: (type_identifier) @identifier_name
             (#eq? @identifier_name "{}"))@node_name""",

    "template_declaration": """
                (template_declaration
                (declaration
                        [
                        (function_declarator
                            (identifier) @identifier_name)

                        (pointer_declarator
                            (function_declarator
                                (identifier) @identifier_name))
                        ]
                                (#eq? @identifier_name "{}"))) @node_name""",

    "declaration": """(declaration
                [
                (function_declarator
                    (identifier) @identifier_name)

                (pointer_declarator
                    (function_declarator
                        (identifier) @identifier_name))
                ]
                     (#eq? @identifier_name "{}")) @node_name""",

    "class_method": """(field_declaration
                    [   (function_declarator
                            (field_identifier) @identifier_name)
                        (pointer_declarator
                            (function_declarator
                                (field_identifier) @identifier_name))
                    ]
                     (#eq? @identifier_name "{}")) @node_name""",
    "initializer_list": """
        (declaration
            (init_declarator
                (identifier) @identifier_name)
        (#eq? @identifier_name "{}")) @node_name
    """
}

cpp_func_queries = {
    "cpp_functions": """
       (function_declarator
                (field_identifier) @identifier_name
                (parameter_list) @params
            )@node_name""",
}

cpp_func_queries.update(c_func_queries)
cpp_decl_queries.update(common_query_dict)
cpp_def_queries.update(common_query_dict)

from tree_sitter import Node
def node_text(node: Optional[Node]) -> str:
    if node is None:
        return ""
    return node.text.decode('utf-8', errors='replace')  # type: ignore

class CPPParser(CParser):
    def __init__(self, file_path: Optional[Path], source_code: Optional[str] = None):

        # preprocess the file path
        # remove the macro between class and class name
        cleaned_code: str = file_path.read_text() if source_code is None and file_path else source_code  # type: ignore
        pattern = r'(\bclass\s+)([A-Z_][A-Z0-9_]*\s+)'  # matches uppercase-style macro
        # Replace the macro with an empty string
        cleaned_code = re.sub(pattern, r'\1', cleaned_code) 
        super().__init__(file_path, cleaned_code, cpp_decl_queries, cpp_def_queries, cpp_func_queries, LanguageType.CPP)
   
    def get_symbol_source(self, symbol_name: str, line: int, lsp_function: LSPFunction) -> tuple[str, str, int]:
        """
        Retrieve the full source code of a symbol based on its start position.
        :param symbol_name: The name of the function to find.
        :param line: The line number of the function's start position (0-based).
        :param column: The column number of the function's start position (0-based).
        :return: The full source code of the function.
        """

        # check if the symbol contains the namespace
        namespace_name = ""
        if "::" in symbol_name:
            # only consider one level namespace for now
            namespace_name = symbol_name.split("::")[-2]
            symbol_name = symbol_name.split("::")[-1]

        # print("language: ", self.project_lang)
        # print("parser_language: ", self.parser_language)
        # type s
        if lsp_function == LSPFunction.Declaration:
            query_dict = self.decl_query_dict
        elif lsp_function == LSPFunction.Definition:
            query_dict = self.def_query_dict
        else:
            print("Unsupported LSP function.")
            return "", "", 0
            
        for key, query_str in query_dict.items():
            # Execute the query
            query_str = query_str.format(symbol_name)
            query = self.parser_language.query(query_str)
            src_node = self.exec_query(query, self.tree.root_node, line)
            if not src_node:
                continue

            # check if the src_node is the correct node filter this kind of line.  class LoggingEvent;
            if key == "classes":
                field_node = self.get_child_node(src_node, ["field_declaration_list"], recusive_flag=False)
                if not field_node:
                    continue
                
            # check if the symbol contains the namespace
            if not namespace_name or lsp_function not in [LSPFunction.Declaration, LSPFunction.Definition]:
                return key, node_text(src_node), src_node.start_point.row 
           
            # compare the namespace name
            if lsp_function == LSPFunction.Definition:
                
                # situation 1: the namespace is before the function like: void A::test()
                name_node = self.get_child_node(src_node, ["namespace_identifier"], recusive_flag=True)
                if not name_node:
                    name_node = self.get_child_node(src_node, ["type_identifier"], recusive_flag=True)

                # if exist the namespace node, then match the namespace
                if name_node:
                    #match the namespace
                    if node_text(name_node) == namespace_name: 
                        return key, node_text(src_node), src_node.start_point.row  
                    else:
                        return "", "", 0              

            # situation 2: the name space is the upper level node
            if lsp_function in [LSPFunction.Declaration, LSPFunction.Definition]:
                # the namespace is the upper level of the class
                # translation_unit is the root node
                parent_node = src_node.parent
                while parent_node and parent_node.type not in ["class_specifier","struct_specifier","union_specifier", "enum_specifier", "translation_unit"]:
                    parent_node = parent_node.parent
              
                # match the namespace
                if parent_node and parent_node.type in ["class_specifier","struct_specifier","union_specifier", "enum_specifier"]:
                    # there may be more identifier other than type_identifier 
                    name_node = self.get_child_node(parent_node, ["type_identifier"], recusive_flag=True)
                    # exist namespace node, then match the namespace
                    if name_node:
                        if node_text(name_node) == namespace_name: 
                            return key, node_text(src_node), src_node.start_point.row 
                        # not match
                        else:
                            return "", "", 0
                else:
                    # the namespace is not found in the class, so we just return the source code
                    return key, node_text(src_node), src_node.start_point.row
            
        return "", "", 0
    
    def get_decl_funcs(self, node: Node, file_path: Path) -> Optional[FunctionDeclaration]:
   
        # Get parameter list
        function_name = node.text.decode('utf-8') # type: ignore
        signature = function_name
        decl_node = self.get_parent_node(node, "declaration")
        function_type = "function"
        if not decl_node:
            decl_node = self.get_parent_node(node, "field_declaration")
            function_type = "class_method"
        
        # may be definition (for .c/.cpp files with function implementations)
        if not decl_node:
            decl_node = self.get_parent_node(node, "function_definition")
            function_type = "function"
        
        if not decl_node:
            return None
        
        signature = decl_node.text.decode('utf-8') # type: ignore

        # Get line number
        line_number = node.start_point[0] + 1
        # Create function declaration object
        func_decl = FunctionDeclaration(
            name=function_name,
            signature=signature,
            file_path=str(file_path),
            line_number=line_number,
            function_type=function_type
        )
       
        if not func_decl or func_decl.function_type == "function":
            return func_decl

        # Try to get the class name for class method
        class_node = self.get_parent_node(node, "class_specifier")
        if class_node:
            # find class name
            name_node = self.get_child_node(class_node, ["type_identifier"], recusive_flag=False)
            if name_node:
                namespace = node_text(name_node)
                func_decl.namespace = namespace
        
        return func_decl

# Example usage
if __name__ == "__main__":
    file_path = Path("/home/yk/code/LLM-reasoning-agents/test/demo.cpp")  # Replace with your C/C++ file path
    line = 3 # Replace with the line number of the function's start position
    column = 0  # Replace with the column number of the function's start position

    # IGRAPH_EXPORT igraph_error_t igraph_read_graph_pajek(igraph_t *graph, FILE *instream);
    # TODO CPP is better for the above function, we should try to use CPP if C is not working
    extractor = CPPParser(file_path)
    extracted_code = extractor.get_symbol_source("WriterAppender::subAppend", 41, LSPFunction.Definition)
    print(extracted_code)

    res = extractor.get_file_functions()
    # sort by line number
    res.sort(key=lambda x: x.line_number)
    for func_info in res:
        print(f"Function info: {func_info.to_dict()}")