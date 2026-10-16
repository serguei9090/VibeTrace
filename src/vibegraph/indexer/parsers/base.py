import hashlib
from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Any

from tree_sitter import Language, Node, Parser, Tree
from tree_sitter_languages import get_language, get_parser

from vibegraph.indexer.db import Edge
from vibegraph.indexer.db import Node as DBNode
from vibegraph.indexer.resolver import ModuleResolver


@dataclass
class ParserContext:
    file_path: str
    nodes: list[DBNode]
    edges: list[Edge]
    file_module_id: str


class LanguageParser(ABC):
    def __init__(self, language_name: str, resolver: ModuleResolver | None = None):
        self.language: Language = get_language(language_name)
        self.parser: Parser = get_parser(language_name)
        self.resolver = resolver

    def parse(self, source_code: bytes) -> Tree:
        return self.parser.parse(source_code)

    def _get_text(self, node: Node | None) -> str:
        """Extract text from a tree-sitter node."""
        if node is None:
            return ""
        return node.text.decode("utf-8")

    def _get_id(self, file_path: str, name: str, kind: str | None = None) -> str:
        """Generate a unique ID for a node."""
        if kind == "module":
            # Modules are identified by their canonical name across the project
            return hashlib.md5(f"module::{name}".encode()).hexdigest()
        return hashlib.md5(f"{file_path}::{name}".encode()).hexdigest()

    def _create_node(
        self,
        node_id: str,
        name: str,
        kind: Any,
        file_path: str,
        node: Node | None,
        signature: str | None = None,
        docstring: str | None = None,
        decorators: list[str] | None = None,
        visibility: str | None = None,
    ) -> DBNode:
        """Create a DBNode with common fields."""
        start_line = node.start_point[0] + 1 if node else 0
        end_line = node.end_point[0] + 1 if node else 0
        return DBNode(
            id=node_id,
            name=name,
            kind=kind,
            file_path=file_path,
            start_line=start_line,
            end_line=end_line,
            signature=signature,
            docstring=docstring,
            decorators=decorators,
            visibility=visibility,
        )

    def _create_edge(self, from_id: str, to_id: str, relation: Any = "defines") -> Edge:
        """Create an edge between two nodes."""
        return Edge(from_node_id=from_id, to_node_id=to_id, relation_type=relation)

    @abstractmethod
    def extract(self, file_path: str, source_code: bytes) -> tuple[list[DBNode], list[Edge]]:
        """Extract nodes and edges from source code."""
        pass
