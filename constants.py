import os

PROJECT_PATH = os.path.dirname(os.path.abspath(__file__))
print(PROJECT_PATH)


class LSPResults():
    """Results of Language Server Protocol."""
    Success = "success"
    Error = "error"
    Retry = "retry"
    NoResult = "no result"
    DockerError = "docker error"


class LanguageType():
    """File types of target files."""
    C = 'C'
    CPP = 'CPP'
    JAVA = 'Java'
    NONE = ''

class LSPFunction():
    Definition = "definition"
    Declaration = "declaration"
    References = "references"
    Header = "header"

class CompileResults:
    Success = "Complie Success"
    CodeError = "Code Error"
    FuzzerError = "No Fuzzer"
    ImageError = "Build Image Error"


class FuzzResult:
    NoError = "No Error"
    Crash = "Crash"
    RunError = "Run Error"
    ReadLogError = "Read Log Error"
    ConstantCoverageError = "Constant Coverage Error"


class ToolDescMode():
    Simple = "simple"
    Detailed = "detailed"


class CodeSearchAPIName():
    """APIs for searching code snippets."""
    Github = "Github"
    # Google = "Google"
    # Bing = "Bing"
    # StackOverflow = "StackOverflow"
    # CodeSearch = "CodeSearch"
    # 
# Entry function for fuzzing.
FuzzEntryFunctionMapping = {
    LanguageType.C: "LLVMFuzzerTestOneInput",
    LanguageType.CPP: "LLVMFuzzerTestOneInput",
    LanguageType.JAVA: "fuzzerTestOneInput",
}

# # Pydantic
