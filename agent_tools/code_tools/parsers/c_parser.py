from constants import LanguageType, LSPFunction
from agent_tools.code_tools.parsers.base_parser import BaseParser
from pathlib import Path
from typing import Optional


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

# related_query_dict = {
#             "normal_ret":   """
#             (
#             declaration
#                 declarator: (function_declarator
#                 declarator: (identifier) @func_name
#                 parameters: (parameter_list
#                     (parameter_declaration
#                     type: (_) @type
#                     declarator: (_) @value))))@node_name
#             """,
#             "pointer_ret": """
#             (
#             declaration
#                 declarator: (pointer_declarator
#                 declarator: (function_declarator
#                 declarator: (identifier) @func_name
#                 parameters: (parameter_list
#                     (parameter_declaration
#                     type: (_) @type
#                     declarator: (_) @value)))))@node_name
#             """,

# }

func_declaration_query_dict = {
  "declaration": """(declaration
                    declarator: (function_declarator
                    declarator: (identifier) @identifier_name
                    )) @node_name""",
                    
    "pointer_declaration": """(declaration
                    declarator: (pointer_declarator
                    declarator: (function_declarator
                    declarator: (identifier) @identifier_name
                    ))) @node_name""",

    "macro_func": """(preproc_function_def
                    name: (identifier) @identifier_name
                    ) @node_name""",
}

def_query_dict = {
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


decl_query_dict = {
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

decl_query_dict.update(common_query_dict)
def_query_dict.update(common_query_dict)

class CParser(BaseParser):
    def __init__(self, file_path: Optional[Path], source_code: Optional[str] = None):
        super().__init__(file_path, source_code,  decl_query_dict, def_query_dict, func_declaration_query_dict, LanguageType.C)

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