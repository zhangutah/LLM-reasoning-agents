from tree_sitter import Language, Parser, Node, Query
import tree_sitter_c  # For C language
import tree_sitter_cpp  # For C++ language
import tree_sitter_java  # For Java language
from constants import LanguageType, FuzzEntryFunctionMapping, LSPFunction
from pathlib import Path
from typing import Optional

parser_language_mapping = {
    LanguageType.C: tree_sitter_c.language(),
    LanguageType.CPP: tree_sitter_cpp.language(),
    LanguageType.JAVA: tree_sitter_java.language(),
}

class BaseParser:
    def __init__(self, file_path: Optional[Path], source_code: Optional[str] = None,
                 decl_query_dict: dict[str, str] = {}, def_query_dict: dict[str, str] = {}, 
                 func_declaration_query_dict: dict[str, str] = {},
                 project_lang: LanguageType = LanguageType.CPP):
        
        self.decl_query_dict = decl_query_dict
        self.def_query_dict = def_query_dict
        self.func_declaration_query_dict = func_declaration_query_dict
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
            LanguageType.JAVA: "method_call",
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
    

    def get_file_functions(self) -> list[tuple[str, str]]:
        ret_list: list[tuple[str, str]] = []
        for _, query in self.func_declaration_query_dict.items():
            # Execute the query
            query = self.parser_language.query(query)
            captures = query.captures(self.tree.root_node)
            if not captures:
                continue

            for source_node in captures["node_name"]:
                # if we can't decode the text, it is meaningless to search
                if not source_node.text:
                    continue
                
                id_node = self.match_child_node(source_node, "identifier", recusive_flag=True)
                # the function name is under function_declarator
                if not id_node or not id_node.text: 
                    continue
                function_name = id_node.text.decode("utf-8", errors="ignore")
                # Decode the source code to a string
                src_code = source_node.text.decode("utf-8", errors="ignore")
                # function declaration must include (
                if src_code:
                    ret_list.append((src_code, function_name))
        
        return ret_list
    
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
                id_node = self.match_child_node(root_node, identifier_str, recusive_flag=True)
                
                # match the function name
                if id_node and id_node.text and pure_symbol_name == id_node.text.decode("utf-8", errors="ignore"): # type: ignore
                    
                    # if the function name matches, check the namespace if any
                    call_str = root_node.text.decode("utf-8", errors="ignore") # type: ignore
                    if "::" in call_str:
                        # split the name space
                        namespace = call_str.split("::")[:-1]
                        if self.match_namespace(namespace, symbol_name.split("::")[:-1]):
                                return id_node
                    else:
                        return id_node
        except Exception:
            pass
        
        return None
    
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

    def get_fuzz_function_node(self, function_name: str) -> Optional[Node]:
        """
        Get the position of a function in the source code.
        :param function_name: The name of the function to find.
        :return: The position of the function in the source code.
        """
        # TODO this only works for call fuzz function directly in the entry function
        # Fist find the Fuzz entry point
        entry_function = FuzzEntryFunctionMapping[self.project_lang]
        entry_node = self.get_definition_node(entry_function)
        return self.get_call_node(function_name, entry_node)
      
    def is_fuzz_function_called(self, function_name: str) -> bool:
        if self.get_fuzz_function_node(function_name):
            return True
        return False
    
    def exist_function_definition(self, function_name: str) -> bool:
        if self.get_definition_node(function_name):
            return True
        return False

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
       

    def match_child_node(self, node:Node, node_type:str, recusive_flag: bool=False) -> Optional[Node]:
        """
        Match the fisrt child node of a given node based on the node type.
        :param node: The parent node to search within.
        :param node_type: The type of the child node to match.
        :param recusive_flag: If True, recursively search through all children.
        :return: The matched child node or None if not found.
        """
        for child in node.children:
            if child.type == node_type:
                return child
            if recusive_flag:
                result = self.match_child_node(child, node_type, recusive_flag)
                if result:
                    return result
        return None 
