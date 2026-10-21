from vibetrace.indexer.parsers.js_parser import JavaScriptParser
from vibetrace.indexer.resolver import ModuleResolver


class TypeScriptParser(JavaScriptParser):
    """Parser for TypeScript and TSX files."""

    def __init__(self, language: str = "typescript", resolver: ModuleResolver | None = None):
        super().__init__(resolver=resolver, language=language)
