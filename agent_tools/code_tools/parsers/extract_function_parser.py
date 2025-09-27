#!/usr/bin/env python3
"""
Extract all function declarations from project header files using tree-sitter and deduplicate them
"""

import json
from pathlib import Path
from typing import Any, List, Dict, Optional
from tree_sitter import Language, Parser
import tree_sitter_c  # For C language
import tree_sitter_cpp  # For C++ language
from constants import LanguageType

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
        self.full_name = f"{namespace}::{name}" if namespace else name
    
    def to_dict(self) -> dict[str, Any]:
        """Convert function declaration to dictionary for JSON serialization"""
        return {
            "name": self.name,
            "signature": self.signature,
            "file_path": self.file_path,
            "line_number": self.line_number,
            "namespace": self.namespace
        }
    
    def __str__(self):
        return f"{self.full_name}: {self.signature} ({self.file_path}:{self.line_number})"
    
    def __repr__(self):
        return f"FunctionDeclaration({self.name}, {self.file_path}:{self.line_number})"
    
    def __eq__(self, other):
        if not isinstance(other, FunctionDeclaration):
            return False
        return self.full_name == other.full_name and self.signature == other.signature
    
    def __hash__(self):
        return hash((self.full_name, self.signature))

class HeaderFunctionExtractor:
    """Header file function declaration extractor"""
    
    def __init__(self, project_root: str = "/src/ada-url", language: Optional[LanguageType] = LanguageType.C):
        self.project_root = Path(project_root)
        self.target_language = language  # If specified, only process files of this language
        self.parser_language_mapping = {
            LanguageType.C: tree_sitter_c.language(),
            LanguageType.CPP: tree_sitter_cpp.language(),
        }
        
        # Tree-sitter query statements for extracting function declarations
        self.function_queries = {
            "function_declaration": """
                (function_declarator
                    (identifier) @function_name
                    (parameter_list) @params
                ) @declaration
            """,
            
            "function_definition": """
                (function_definition
                    (function_declarator
                        (identifier) @function_name
                        (parameter_list) @params
                    )
                ) @definition
            """,
            
            "method_declaration": """
                (field_declaration
                    (function_declarator
                        (field_identifier) @function_name
                        (parameter_list) @params
                    )
                ) @declaration
            """,
            
            "template_function": """
                (template_declaration
                    (function_definition
                        (function_declarator
                            (identifier) @function_name
                            (parameter_list) @params
                        )
                    )
                ) @declaration
            """,
            
            "template_function_declaration": """
                (template_declaration
                    (declaration
                        (function_declarator
                            (identifier) @function_name
                            (parameter_list) @params
                        )
                    )
                ) @declaration
            """,
            
            "constructor_declaration": """
                (field_declaration
                    (function_declarator
                        (field_identifier) @function_name
                        (parameter_list) @params
                    )
                ) @declaration
            """,
            
            "destructor_declaration": """
                (field_declaration
                    (function_declarator
                        (field_identifier) @function_name
                        (parameter_list) @params
                    )
                ) @declaration
            """
        }
        
        # Simplified query statements
        self.simple_queries = {
            "all_functions": """
                [
                    (function_declarator
                        (identifier) @function_name
                        (parameter_list) @params
                    )
                    (function_declarator
                        (field_identifier) @function_name
                        (parameter_list) @params
                    )
                ] @declaration
            """,
            
            "namespaces": """
                (namespace_definition
                    (namespace_identifier) @namespace_name
                ) @namespace
            """,
            
            "classes": """
                (class_specifier
                    (type_identifier) @class_name
                ) @class
            """
        }
    
    def find_header_files(self) -> List[Path]:
        """Find all header files"""
        header_files = []
        # Include all supported file types if no specific language is specified
        patterns = ["**/*.h", "**/*.hpp", "**/*.hh", "**/*.hxx", "**/*.c", "**/*.cpp", "**/*.cc", "**/*.cxx"]

        # Find header files in project root
        if self.project_root.exists():
            for pattern in patterns:
                # exclude third_party
                for file in self.project_root.glob(pattern):
                    if "third_party" in str(file):
                        continue
                    header_files.append(file)

        return sorted(list(set(header_files)))
    
    def extract_functions_from_file(self, file_path: Path) -> List[FunctionDeclaration]:
        """Extract function declarations from a single file"""
        
        # Read file content
        with open(file_path, 'r', encoding='utf-8', errors='ignore') as f:
            source_code = f.read()
        
        # Setup parser
        language = Language(self.parser_language_mapping[self.target_language])
        parser = Parser(language)
        
        # Parse code
        tree = parser.parse(bytes(source_code, 'utf-8'))
        
        functions = []
        
        # Use simplified query to extract functions
        query_text = self.simple_queries["all_functions"]
        query = language.query(query_text)
        
        captures = query.captures(tree.root_node)
        
        # Check if there are captures
        if not captures:
            return functions
            
        # Extract function names from captures
        for capture_name, nodes in captures.items():
            if capture_name == "function_name":
                for node in nodes:
                    function_name = node.text.decode('utf-8')
                    
                    # Get parameter list
                    params_node = None
                    for sibling in node.parent.children:
                        if sibling.type == "parameter_list":
                            params_node = sibling
                            break
                    
                    if params_node:
                        params_text = params_node.text.decode('utf-8')
                        signature = f"{function_name}{params_text}"
                    else:
                        signature = function_name
                    
                    # Get line number
                    line_number = node.start_point[0] + 1
                    
                    # Create function declaration object
                    func_decl = FunctionDeclaration(
                        name=function_name,
                        signature=signature,
                        file_path=str(file_path),
                        line_number=line_number,
                        function_type="function"
                    )
                    
                    functions.append(func_decl)
        
        return functions
            
    
    def extract_all_functions(self) -> Dict[str, List[FunctionDeclaration]]:
        """Extract function declarations from all header files"""
        header_files = self.find_header_files()
        print(f"Found {len(header_files)} header files")
        
        all_functions = {}
        
        for file_path in header_files:
            print(f"Processing: {file_path}")
            functions = self.extract_functions_from_file(file_path)
            if functions:
                all_functions[str(file_path)] = functions
        
        return all_functions
    
    def deduplicate_functions(self, all_functions: Dict[str, List[FunctionDeclaration]]) -> Dict[str, FunctionDeclaration]:
        """Deduplicate functions"""
        unique_functions = {}
        
        for file_path, functions in all_functions.items():
            for func in functions:
                key = func.full_name
                if key not in unique_functions:
                    unique_functions[key] = func
                else:
                    # If already exists, keep the one with more complete signature
                    existing = unique_functions[key]
                    if len(func.signature) > len(existing.signature):
                        unique_functions[key] = func
        
        return unique_functions
    
    def save_as_json(self, unique_functions: Dict[str, FunctionDeclaration], output_file: Path) -> None:
        """Save function data as JSON"""

        # Convert functions to list of dictionaries
        function_list = []
        for func in unique_functions.values():
            function_list.append(func.to_dict())

        # Sort functions by name for consistent output
        function_list.sort(key=lambda x: x["name"])

        # Save to JSON file
        with open(output_file, 'w', encoding='utf-8') as f:
            json.dump(function_list, f, indent=2, ensure_ascii=False)
        

def main():
    """Main function"""
    import argparse
    
    # Setup argument parser
    parser = argparse.ArgumentParser(description="Extract function declarations from header files")
    parser.add_argument("--project-root", default="/src/igraph", 
                       help="Root directory of the project (default: /src/igraph)")
    parser.add_argument("--language", choices=["c", "cpp"], default="c",
                       help="Target language (c or cpp). If not specified, all supported languages will be processed")
    parser.add_argument("--output", default="function_declarations.json",
                       help="Output JSON file name (default: function_declarations.json)")
    
    args = parser.parse_args()
    
    # Convert language argument to LanguageType
    target_language = None
    if args.language:
        if args.language == "c":
            target_language = LanguageType.C
        elif args.language == "cpp":
            target_language = LanguageType.CPP
    
    print("Starting function declaration extraction...")
    if target_language:
        print(f"Target language: {args.language}")
    else:
        print("Processing all supported languages (C and C++)")
    
    # Create extractor
    extractor = HeaderFunctionExtractor(project_root=args.project_root, language=target_language)

    # Extract all functions
    all_functions = extractor.extract_all_functions()
    
    # Deduplicate
    unique_functions = extractor.deduplicate_functions(all_functions)
    
    # Save as JSON
    output_file = Path(args.project_root) / args.output
    extractor.save_as_json(unique_functions, output_file)
    
    print(f"Extraction complete! Found {len(unique_functions)} unique functions")
    print(f"Data saved to: {output_file}")
    
    # Print statistics
    print("\nStatistics:")
    print(f"- Total files: {len(all_functions)}")
    print(f"- Total functions: {sum(len(funcs) for funcs in all_functions.values())}")
    print(f"- Unique functions: {len(unique_functions)}")


if __name__ == "__main__":
    main()
