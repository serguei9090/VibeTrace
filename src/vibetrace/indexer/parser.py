"""
Backward compatibility shim for vibetrace.indexer.parser.
Decomposed into vibetrace.indexer.parsers.*.
"""

from vibetrace.indexer.parsers import (
    GenericParser,
    GoParser,
    JavaScriptParser,
    LanguageParser,
    ParserContext,
    ParserFactory,
    PythonParser,
    RustParser,
    TypeScriptParser,
)

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
