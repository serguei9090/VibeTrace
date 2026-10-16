"""
Backward compatibility shim for vibegraph.indexer.parser.
Decomposed into vibegraph.indexer.parsers.*.
"""

from vibegraph.indexer.parsers import (
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
