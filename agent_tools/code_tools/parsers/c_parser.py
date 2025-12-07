from constants import LanguageType, LSPFunction
from agent_tools.code_tools.parsers.base_parser import BaseParser, FunctionDeclaration
from pathlib import Path
from typing import Optional
from tree_sitter import Node

common_query_dict = {
    "macro_func": """(preproc_function_def
                    name: (identifier) @identifier_name
                     (#eq? @identifier_name "{}")
                    ) @node_name""",

    "macro_definition": """(preproc_def
                    name: (identifier) @identifier_name
                    (#eq? @identifier_name "{}")) @node_name""",

    "type_definition": """(type_definition
                        [(type_identifier) @identifier_name
                        (pointer_declarator
                            (type_identifier) @identifier_name)
                        ]
                    (#eq? @identifier_name "{}")) @node_name""",

    "struct": """(struct_specifier
                    name: (type_identifier) @identifier_name
                    (#eq? @identifier_name "{}")) @node_name""",
    "union": """(union_specifier
                    name: (type_identifier) @identifier_name
                    (#eq? @identifier_name "{}")) @node_name""",
    "enum": """(enum_specifier
                    name: (type_identifier) @identifier_name
                    (#eq? @identifier_name "{}")) @node_name""",
    "enum_dedefinition": """(enum_specifier
                        name: (type_identifier) @enum.name
                        body: (enumerator_list
                                (enumerator
                                name: (identifier) @identifier_name
                                (#eq? @identifier_name "{}")
                                )
                            ) @enum.body
                    ) @node_name""",
    "anonymous_enum_dedefinition": """(enum_specifier
                    body: (enumerator_list
                            (enumerator
                            name: (identifier) @identifier_name
                            (#eq? @identifier_name "{}")
                            )
                        ) @enum.body
                ) @node_name""",
    "extern_type": """(declaration

                    [  
                    (identifier) @identifier_name
                    
                        (pointer_declarator
                            (identifier) @identifier_name
                        )
                     ]
                    (#eq? @identifier_name "{}")
                    ) @node_name""",

    "field_declaration": """(field_declaration
                    [(pointer_declarator
                        (field_identifier) @identifier_name)

                     (field_identifier) @identifier_name
                    ]
                    (#eq? @identifier_name "{}")
                    )@node_name""",


    "typedef_arr": """(type_definition
                    [(array_declarator
                        (type_identifier) @identifier_name)

                    (pointer_declarator
                        (array_declarator
                            (type_identifier) @identifier_name)) ]
          
                    (#eq? @identifier_name "{}")
                    )@node_name""",

    "typedef_function_pointer": """(type_definition
                                    (function_declarator
                                        (parenthesized_declarator
                                            (pointer_declarator
                                                (type_identifier) @identifier_name
                                    )))
                                    (#eq? @identifier_name "{}"))@node_name"""

}



c_def_queries = {
    "fucntion":"""(function_definition
                    declarator: (function_declarator
                    declarator: (identifier) @identifier_name
                    (#eq? @identifier_name "{}")
                    )) @node_name""",
    "pointer_function": """(function_definition
                    declarator: (pointer_declarator
                    declarator: (function_declarator
                    declarator: (identifier) @identifier_name
                    (#eq? @identifier_name "{}")
                    ))) @node_name""",
                    
        } 


c_decl_queries = {
    "declaration": """(declaration
                    declarator: (function_declarator
                    declarator: (identifier) @identifier_name
                     (#eq? @identifier_name "{}")
                    )) @node_name""",
    "pointer_declaration": """(declaration
                    declarator: (pointer_declarator
                    declarator: (function_declarator
                    declarator: (identifier) @identifier_name
                     (#eq? @identifier_name "{}")
                    ))) @node_name""",
}


c_func_queries = {
    "c_functions": """
            (function_declarator
                (identifier) @identifier_name
                (parameter_list) @params
            )@node_name""",
}


c_decl_queries.update(common_query_dict)
c_def_queries.update(common_query_dict)

class CParser(BaseParser):
    def __init__(self, file_path: Optional[Path], source_code: Optional[str] = None, 
                 decl_query_dict: dict[str, str] = c_decl_queries,
                 def_query_dict: dict[str, str] = c_def_queries, 
                 func_query_dict: dict[str, str] = c_func_queries,
                 project_lang: LanguageType = LanguageType.C):
        super().__init__(file_path, source_code,  decl_query_dict, def_query_dict, func_query_dict, project_lang)

    def get_identifier_name_under_call(self, root_node:Node) -> str:

        # C/C++ function name is the first child of the call expression
        id_node = self.get_child_node(root_node, ["identifier", "field_identifier"], recusive_flag=True)
        if id_node:
            return id_node.text.decode("utf-8", errors="ignore") # type: ignore
        return ""
        
    def get_definition_node(self, function_name: str) -> Optional[Node]:
        # Define a query to find "function_definition" nodes
        function_definition_query = self.parser_language.query(f"({self.func_def_name}) @func_def")

        # Execute the query
        captures = function_definition_query.captures(self.tree.root_node)
        if not captures:
            return None
        # Check the nodes
        for node in captures["func_def"]:
            
            decl_node = self.get_child_node(node, ["function_declarator"], recusive_flag=True)
            if not decl_node:
                continue
            id_node = self.get_identifier_node(decl_node, function_name)
            if id_node:
                return node
        
        return None
    
    def match_namespace(self, ns1: list[str], ns2: list[str]) -> bool:
        """
        Match two namespace lists.They don't have to be exactly the same, but one should be the suffix of the other.
        :param ns1: The first namespace list.
        :param ns2: The second namespace list.
        """
        for na, nb in zip(reversed(ns1), reversed(ns2)):
            if na != nb:
                return False
        return True
    
    def get_identifier_node(self, root_node:Node, symbol_name: str) -> Optional[Node]:
       
         # remove the namespace
        if "::" in symbol_name:
            pure_symbol_name = symbol_name.split("::")[-1]
        else:
            pure_symbol_name = symbol_name

        try:
            # TODO C/C++ function name is the first child of the call expression
            for identifier_str in ["identifier", "field_identifier"]:
                id_node = self.get_child_node(root_node, [identifier_str], recusive_flag=True)
                
                # match the function name
                if id_node and id_node.text and pure_symbol_name == id_node.text.decode("utf-8", errors="ignore"): # type: ignore
                    
                    # if the function name matches, check the namespace if any
                    call_str = root_node.text.decode("utf-8", errors="ignore") # type: ignore
                    call_prefix = call_str.split(pure_symbol_name)[0]
                    call_prefix = call_prefix.strip()
                    if call_prefix.endswith("::"):
                        # split the name space, the last element is empty
                        namespace = call_prefix.split("::")[:-1]
                        if self.match_namespace(namespace, symbol_name.split("::")[:-1]):
                            return id_node
                    else:
                        return id_node
        except Exception:
            pass
        
        return None
    
    def get_decl_funcs(self, node: Node, file_path: Path) -> Optional[FunctionDeclaration]:
   
        # Get parameter list
        function_name = node.text.decode('utf-8') # type: ignore
        signature = function_name
        decl_node = self.get_parent_node(node, "declaration")
        function_type = "function"
        # may be definition 
        if not decl_node:
            # Also check for function_definition (for .c files with function implementations)
            decl_node = self.get_parent_node(node, "function_definition")
        
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
        return func_decl
    

# Example usage
if __name__ == "__main__":
    file_path = Path("/home/yk/code/LLM-reasoning-agents/test/demo.c")  # Replace with your C/C++ file path
    line = 101 # Replace with the line number of the function's start position
    column = 0  # Replace with the column number of the function's start position

    # IGRAPH_EXPORT igraph_error_t igraph_read_graph_pajek(igraph_t *graph, FILE *instream);
    # TODO CPP is better for the above function, we should try to use CPP if C is not working
    extractor = CParser(file_path)
    extracted_code = extractor.get_symbol_source("bpf_object__open_mem", line, LSPFunction.Declaration)
    print("Function source code:")
    print(extracted_code)