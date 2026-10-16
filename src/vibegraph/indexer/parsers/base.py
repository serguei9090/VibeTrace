import hashlib
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
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
    scope_stack: list[str] = field(default_factory=list)
    # Maps local symbol/alias -> (resolved_file_path, remote_qualified_name_or___module__)
    imported_symbols: dict[str, tuple[str, str]] = field(default_factory=dict)


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

    def _get_id(self, file_path: str, qualified_name: str, kind: str | None = None) -> str:
        """Generate a unique deterministic ID for a node using its scoped qualified_name."""
        if kind == "module":
            return hashlib.md5(f"module::{qualified_name}".encode()).hexdigest()
        return hashlib.md5(f"{file_path}::{qualified_name}".encode()).hexdigest()

    def _scoped_name(self, name: str, scope_stack: list[str] | None = None) -> str:
        """Build a dotted qualified name from the active scope stack."""
        if scope_stack:
            return ".".join([*scope_stack, name])
        return name

    def _resolve_call_target(self, called_name: str, ctx: ParserContext) -> str:
        """Resolve a called symbol name to its target node ID (intra-file or cross-file)."""
        # 1. Method calls on self / this inside an enclosing class scope
        if called_name.startswith(("self.", "this.")) and ctx.scope_stack:
            member = called_name.split(".", 1)[1].split(".")[0]
            enclosing = ctx.scope_stack[0]
            return self._get_id(ctx.file_path, f"{enclosing}.{member}")

        # 2. Direct imported symbol call: e.g. `from b import helper` -> `helper()`
        if called_name in ctx.imported_symbols:
            target_file, target_sym = ctx.imported_symbols[called_name]
            return self._get_id(target_file, target_sym)

        # 3. Qualified call on imported module or class: e.g. `db.upsert_node()` or `ClassB.run()`
        if "." in called_name:
            prefix, rest = called_name.split(".", 1)
            member = rest.split(".")[0]
            if prefix in ctx.imported_symbols:
                target_file, target_sym = ctx.imported_symbols[prefix]
                if target_sym == "__module__":
                    return self._get_id(target_file, member)
                return self._get_id(target_file, f"{target_sym}.{member}")

        # 4. Default: symbol in the current file
        return self._get_id(ctx.file_path, called_name)

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
        qualified_name: str | None = None,
        parent_id: str | None = None,
    ) -> DBNode:
        """Create a DBNode with common fields."""
        start_line = node.start_point[0] + 1 if node else 0
        end_line = node.end_point[0] + 1 if node else 0
        return DBNode(
            id=node_id,
            name=name,
            qualified_name=qualified_name or name,
            parent_id=parent_id,
            kind=kind,
            file_path=file_path,
            start_line=start_line,
            end_line=end_line,
            signature=signature,
            docstring=docstring,
            decorators=decorators,
            visibility=visibility,
        )

    def _create_edge(
        self,
        from_id: str,
        to_id: str,
        relation: Any = "defines",
        node: Node | None = None,
        call_line: int | None = None,
    ) -> Edge:
        """Create an edge between two nodes, optionally recording the 1-indexed call line."""
        line = call_line if call_line is not None else (node.start_point[0] + 1 if node else None)
        return Edge(from_node_id=from_id, to_node_id=to_id, relation_type=relation, call_line=line)

    @abstractmethod
    def extract(self, file_path: str, source_code: bytes) -> tuple[list[DBNode], list[Edge]]:
        """Extract nodes and edges from source code."""
        pass
