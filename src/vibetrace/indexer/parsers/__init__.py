from vibetrace.indexer.parsers.base import LanguageParser, ParserContext
from vibetrace.indexer.parsers.js_ts_parser import JavaScriptParser, TypeScriptParser
from vibetrace.indexer.parsers.python_parser import PythonParser
from vibetrace.indexer.parsers.systems_parser import GenericParser, GoParser, RustParser
from vibetrace.indexer.resolver import ModuleResolver


class ParserFactory:
    @staticmethod
    def get_parser(file_path: str, resolver: ModuleResolver | None = None) -> LanguageParser | None:
        if file_path.endswith(".py"):
            return PythonParser(resolver)
        elif file_path.endswith((".js", ".jsx")):
            return JavaScriptParser(resolver)
        elif file_path.endswith(".ts"):
            return TypeScriptParser("typescript", resolver)
        elif file_path.endswith(".tsx"):
            return TypeScriptParser("tsx", resolver)
        elif file_path.endswith(".go"):
            return GoParser(resolver)
        elif file_path.endswith(".rs"):
            return RustParser(resolver)
        elif file_path.endswith(".java"):
            return GenericParser("java", resolver)
        elif file_path.endswith((".c", ".h")):
            return GenericParser("c", resolver)
        elif file_path.endswith((".cpp", ".cc", ".cxx", ".hpp")):
            return GenericParser("cpp", resolver)
        elif file_path.endswith(".cs"):
            return GenericParser("c_sharp", resolver)
        elif file_path.endswith(".rb"):
            return GenericParser("ruby")
        elif file_path.endswith(".php"):
            return GenericParser("php")
        return None


__all__ = [
    "GenericParser",
    "GoParser",
    "JavaScriptParser",
    "LanguageParser",
    "ParserContext",
    "ParserFactory",
    "PythonParser",
    "RustParser",
    "TypeScriptParser",
]
