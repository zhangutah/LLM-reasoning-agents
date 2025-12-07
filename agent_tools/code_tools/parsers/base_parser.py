from tree_sitter import Language, Parser, Node, Query
import tree_sitter_c  # For C language
import tree_sitter_cpp  # For C++ language
import tree_sitter_java  # For Java language
from constants import LanguageType, FuzzEntryFunctionMapping, LSPFunction
from pathlib import Path
from typing import Optional, Any

parser_language_mapping = {
    LanguageType.C: tree_sitter_c.language(),
    LanguageType.CPP: tree_sitter_cpp.language(),
    LanguageType.JAVA: tree_sitter_java.language(),
}

class FunctionDeclaration:
    """Function declaration information"""
    def __init__(self, name: str, signature: str, file_path: str, line_number: int, 
                 function_type: str = "function", namespace: str = ""):
        self.name = name
        self.signature = signature
        self.file_path = file_path
        self.line_number = line_number
        self.function_type = function_type
        self.namespace = namespace
        self.full_name = f"{namespace}::{signature}" if namespace else signature
    
    def to_dict(self) -> dict[str, Any]:
        """Convert function declaration to dictionary for JSON serialization"""
        return {
            "name": self.name,
            "signature": self.signature,
            "file_path": self.file_path,
            "line_number": self.line_number,
            "namespace": self.namespace,
            "function_type": self.function_type,
        }
    

class BaseParser:
    def __init__(self, file_path: Optional[Path], source_code: Optional[str] = None,
                 decl_query_dict: dict[str, str] = {}, def_query_dict: dict[str, str] = {}, 
                 func_query_dict: dict[str, str] = {},
                 project_lang: LanguageType = LanguageType.CPP):
        
        self.decl_query_dict = decl_query_dict
        self.def_query_dict = def_query_dict
        self.func_query_dict = func_query_dict
        self.file_path = file_path
        self.project_lang = project_lang
        self.parser_language = self.set_language(project_lang)
        self.parser = Parser(self.parser_language)

        if source_code:
            assert isinstance(source_code, str)
            self.source_code = bytes(source_code, "utf-8")
        elif file_path:
            self.source_code = file_path.read_bytes()
        else:
            raise ValueError("Either source code or file path must be provided.")
        
        # for fuzzing
        self.call_func_name, self.func_def_name = self.name_mapping()
        self.tree = self.parser.parse(self.source_code)

    def set_language(self, language: LanguageType) -> Language:
        assert language in parser_language_mapping.keys(), f"Language {language} not supported."
        return Language(parser_language_mapping[language])

    def name_mapping(self):
        call_name_dict = {
            LanguageType.C: "call_expression",
            LanguageType.CPP: "call_expression",
            LanguageType.JAVA: "method_invocation",
        }
        func_def_name_dict = {
            LanguageType.C: "function_definition",
            LanguageType.CPP: "function_definition",
            LanguageType.JAVA: "method_declaration",
        }

        return call_name_dict[self.project_lang], func_def_name_dict[self.project_lang]

    def exec_query(self, query: Query, query_node: Node, line: int, node_name:str="node_name") -> Optional[Node]:
        # Execute the query
        captures = query.captures(query_node)
        if not captures:
            return None
        
        for source_node in captures[node_name]:
        
            # TODO will this find the definition that calls the function?
            if not source_node.text:
                continue
            if source_node.start_point.row <= line and line <= source_node.end_point.row:  
                return source_node
        return None
    
    def get_symbol_source(self, symbol_name: str, line: int, lsp_function: LSPFunction) -> tuple[str, str, int]:
        """
        Retrieve the full source code of a symbol based on its start position.
        :param symbol_name: The name of the function to find.
        :param line: The line number of the function's start position (0-based).
        :param column: The column number of the function's start position (0-based).
        :return: The full source code of the function.
        """
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
            if src_node:
                # Decode the source code to a string
                return key, src_node.text.decode(), src_node.start_point.row # type: ignore

        return "", "", 0
    

    # def get_file_functions(self) -> list[tuple[str, str]]:
    #     ret_list: list[tuple[str, str]] = []
    #     for _, query in self.func_declaration_query_dict.items():
    #         # Execute the query
    #         query = self.parser_language.query(query)
    #         captures = query.captures(self.tree.root_node)
    #         if not captures:
    #             continue

    #         for source_node in captures["node_name"]:
    #             # if we can't decode the text, it is meaningless to search
    #             if not source_node.text:
    #                 continue

    #             id_node = self.match_child_node(source_node, ["identifier", "field_identifier"], recusive_flag=True)
    #             # the function name is under function_declarator
    #             if not id_node or not id_node.text: 
    #                 continue
    #             function_name = id_node.text.decode("utf-8", errors="ignore")
    #             # Decode the source code to a string
    #             src_code = source_node.text.decode("utf-8", errors="ignore")
    #             # function declaration must include (
    #             if src_code:
    #                 ret_list.append((src_code, function_name))
        
    #     return ret_list
    def get_identifier_node(self, root_node:Node, symbol_name: str) -> Optional[Node]:
        raise NotImplementedError("This method should be implemented in subclasses.")
    def get_identifier_name_under_call(self, root_node:Node) -> str:
        raise NotImplementedError("This method should be implemented in subclasses.")
    def get_definition_node(self, function_name: str) -> Optional[Node]:
        raise NotImplementedError("This method should be implemented in subclasses.")
    def get_decl_funcs(self, node: Node, file_path: Path) -> Optional[FunctionDeclaration]:
        raise NotImplementedError("This method should be implemented in subclasses.")
    
    def get_ref_source(self, symbol_name: str, line: int) -> str:

        # find the callee node
        callee_node = None
        query = self.parser_language.query(f"({self.call_func_name}) @func_call")

        # Execute the query
        captures = query.captures( self.tree.root_node)

        if not captures:
            return ""

        # Print the nodes
        for node in captures["func_call"]:
            # if we can't decode the text, it is meaningless to search
            if not node.text:
                continue
            
            callee_node = self.get_identifier_node(node, symbol_name)
            if callee_node:
                break
        
        if not callee_node:
            return ""

        # all the way to the top of the first function definition
        while callee_node.parent:
            callee_node = callee_node.parent
            if callee_node.type == self.func_def_name:
                break
        if callee_node.text:
            return callee_node.text.decode("utf-8", errors="ignore")

        return ""

        # find the upper node of the callee node, which is reference node


    def get_call_node(self, function_name: str, entry_node: Optional[Node] = None) -> Optional[Node]:
        if not entry_node:
            print("Entry function not found.")
            return None

        # Define a query to find "function_call" nodes
        function_call_query = self.parser_language.query(f"({self.call_func_name}) @func_call")

        # Execute the query
        captures = function_call_query.captures(entry_node)
        if not captures:
            return None
            
        # Print the nodes
        for node in captures["func_call"]:
            id_node = self.get_identifier_node(node, function_name)
            if id_node:
                return node
        return None
       
    def get_fuzz_function_node(self, function_name: str, expression_flag: bool = False) -> Optional[Node]:
        """
        Get the position of a function in the source code.
        :param function_name: The name of the function to find.
        :return: The position of the function in the source code.
        """
        call_node = self.get_call_node(function_name, self.tree.root_node)
        if not expression_flag:
            return call_node
        
        # return the expression node
        if not call_node:
            return None
        
        # find parent expression node
        while call_node.parent:
            call_node = call_node.parent
            # local_variable_declaration for java
            # expression_statement and declaration for C/C++
            if call_node.type in ["expression_statement", "declaration", "variable_declarator"]:
                return call_node
            
            if call_node.type == "translation_unit":
                break
        return None


    def get_parent_node(self, call_node:Node, type_name: str) -> Optional[Node]:
        
        # get the parent definition node of the call node
        while call_node.parent:
            call_node = call_node.parent
            if call_node.type == type_name:
                return call_node
            # the top node
            if call_node.type == "translation_unit":
                break
        return None
    
    def get_child_node(self, node:Node, node_type:list[str], recusive_flag: bool=False) -> Optional[Node]:
        """
        Match the fisrt child node of a given node based on the node type.
        :param node: The parent node to search within.
        :param node_type: The type of the child node to match.
        :param recusive_flag: If True, recursively search through all children.
        :return: The matched child node or None if not found.
        """
        for child in node.children:
            if child.type in node_type:
                return child
            if recusive_flag and child.type != "ERROR":
                result = self.get_child_node(child, node_type, recusive_flag)
                if result:
                    return result
        return None 
    
    def is_function_called(self, function_name: str) -> bool:

        # this prevents infinite loop
        visited_nodes: list[str] = []
        def_node_name = function_name
        while def_node_name != FuzzEntryFunctionMapping[self.project_lang]:
            call_node = self.get_call_node(def_node_name, self.tree.root_node)
            # check if the call node is under the main function
            if not call_node:
                return False
            
            # no definition node found
            def_node = self.get_parent_node(call_node, self.func_def_name)
            if not def_node:
                return False

            # no identifier name found
            def_node_name = self.get_identifier_name_under_call(def_node)
            if not def_node_name:
                return False
            if def_node_name in visited_nodes:
                return False
            visited_nodes.append(def_node_name)

        return True
    
    def is_function_defined(self, function_name: str) -> bool:
        if self.get_definition_node(function_name):
            return True
        return False

    
    def get_file_functions(self) -> list[FunctionDeclaration]:
        """Extract function declarations from a single file"""
        
        functions: list[FunctionDeclaration] = []
        # Use simplified query to extract functions

        for _, query_str in self.func_query_dict.items():
            # Execute the query
            query = self.parser_language.query(query_str)
            captures = query.captures(self.tree.root_node)
            
            # Check if there are captures - continue to next query instead of returning early
            if not captures or "identifier_name" not in captures:
                continue
                
            # Extract function names from captures
            for node in captures["identifier_name"]:
              
                # TODO treat cpp method as function for now
                func_decl = self.get_decl_funcs(node, self.file_path) # type: ignore
                if func_decl:
                    functions.append(func_decl)
        
        return functions
            
    
