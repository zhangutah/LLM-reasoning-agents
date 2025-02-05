from tree_sitter import Language, Parser
import tree_sitter_c  # For C language
import tree_sitter_cpp  # For C++ language
import tree_sitter_java  # For Java language
from constants import LanguageType, FuzzEntryFunctionMapping, LSPFunction

parser_language_mapping = {
    LanguageType.C: tree_sitter_c.language(),
    LanguageType.CPP: tree_sitter_cpp.language(),
    LanguageType.JAVA: tree_sitter_java.language(),
}

class LanguageParser:
    def __init__(self, file_path: str, source_code: str = None, project_lang: LanguageType = LanguageType.CPP):
        self.file_path = file_path
        self.project_lang = project_lang
        self.parser_language = self.set_language(project_lang)
        self.parser = Parser(self.parser_language)

        assert source_code or file_path, "Either source code or file path must be provided."

        if source_code:
            assert isinstance(source_code, str)
            self.source_code = bytes(source_code, "utf-8")
        else:
            with open(file_path, "rb") as f:
                self.source_code = f.read()

        self.tree = self.parser.parse(self.source_code)

    def set_language(self, language: str):
        assert language in parser_language_mapping.keys(), f"Language {language} not supported."
        return Language(parser_language_mapping[language])


    def get_symbol_source(self, symbol_name: str, line: int, lsp_function: str) -> str:
        """
        Retrieve the full source code of a symbol based on its start position.
        :param symbol_name: The name of the function to find.
        :param line: The line number of the function's start position (0-based).
        :param column: The column number of the function's start position (0-based).
        :return: The full source code of the function.
        """
        # Define a query to find "definition" and "declaration" nodes
        #  TODO: Test on other languages. Only tested on C/C++.
        definition_query = self.parser_language.query("""
        (function_definition) @func_decl
        """)
        declaration_query = self.parser_language.query("""
        (declaration) @func_decl
        """)
        type_definition_query = self.parser_language.query("""
        (type_definition) @func_decl
        """)
        struct_specifier_query = self.parser_language.query("""
        (struct_specifier) @func_decl
        """)
        enum_specifier_query = self.parser_language.query("""
        (enum_specifier) @func_decl
        """)


        # type s
        if lsp_function == LSPFunction.Declaration:
            query_list = [declaration_query, struct_specifier_query, enum_specifier_query]
        elif lsp_function == LSPFunction.Definition:
            query_list = [type_definition_query, struct_specifier_query, definition_query]

        for query in query_list:

            # Execute the query
            captures = query.captures(self.tree.root_node)
            
            if not captures:
                continue
            
            # Print the nodes
            for node in captures["func_decl"]:
                source_code = node.text.decode("utf-8")
                # 
                if node.start_point.row <= line and  line <= node.end_point.row and symbol_name in source_code:
                    return source_code

    def get_ref_source(self, symbol_name: str, line: int) -> str:

        # find the callee node
        callee_node = None
        query = self.parser_language.query("""
        (call_expression) @func_call
        """)

        # Execute the query
        captures = query.captures( self.tree.root_node)

        if not captures:
            return []

        # Print the nodes
        for node in captures["func_call"]:
            source_code = node.text.decode("utf-8")
            # 
            if (node.start_point.row == line or line == node.end_point.row) and symbol_name in source_code:
                callee_node = node
                break
        
        if not callee_node:
            return []

        try:
            while callee_node.parent:
                callee_node = callee_node.parent
                if callee_node.type == "function_definition":
                    break

            return callee_node.text.decode("utf-8")
        except Exception as e:
            print("Error in finding the reference source: ", e)

        # find the upper node of the callee node, which is reference node
        


    def is_called(self, function_name: str) -> bool:
        """
        Check if a function is called inside the fuzz function.
        :param function_name: The name of the function to find.
        :return: True if the function is called, False otherwise.
        """
        # TODO this does not support class constructors yet
        # TODO this only test on C/C++ language

        # Fist find the Fuzz entry point
        entry_function = FuzzEntryFunctionMapping[self.project_lang]
        entry_node = self.match_definition_node(entry_function)
        if not entry_node:
            print("Entry function not found.")
            return False

        # Define a query to find "function_call" nodes
        function_call_query = self.parser_language.query("""
        (call_expression) @func_call
        """)

        # Execute the query
        captures = function_call_query.captures(entry_node)
        if not captures:
            return False
            
        # Print the nodes
        for node in captures["func_call"]:
            try:
                # function name is the first child of the call expression
                if function_name == node.children[0].text.decode("utf-8"):
                    return True
            except Exception as e:
                print("Error in parsing the function call: ", e)
        return False

    def have_definition(self, function_name: str) -> bool:
        if self.match_definition_node(function_name):
            return True
        return False

    def match_definition_node(self, function_name: str):
        # TODO this only test on C/C++ language
        
        # Define a query to find "function_definition" nodes
        function_definition_query = self.parser_language.query("""
        (function_definition) @func_def
        """)

        # Execute the query
        captures = function_definition_query.captures(self.tree.root_node)

        # Check the nodes
        for node in captures["func_def"]:

            try:
                for child in node.children:
                    if child.type != "function_declarator":
                        continue
                    
                    # the function name is under function_declarator
                    if function_name == child.children[0].text.decode("utf-8"):
                        return node
            except Exception as e:
                print("Error in parsing the function definition: ", e)
        
        return None
       

# Example usage
if __name__ == "__main__":
    file_path = "tools/demo.c"  # Replace with your C/C++ file path
    line = 46  # Replace with the line number of the function's start position
    column = 2  # Replace with the column number of the function's start position

    extractor = LanguageParser(file_path, project_lang="C")
    # function_code = extractor.is_called("add")
    # is_called = extractor.have_definition("add")
    extracted_code = extractor.get_symbol_source("DNS_DECOMPRESS_NEVER", 16, "declaration")
    print("Function source code:")
    print(extracted_code)