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
    # Maps local variable/param name -> ClassType string
    local_types: dict[str, str] = field(default_factory=dict)
    # Set of symbol names defined in this file (top-level functions, classes, etc.)
    defined_symbols: set[str] = field(default_factory=set)


PYTHON_BUILTINS: set[str] = {
    "abs",
    "aiter",
    "all",
    "anext",
    "any",
    "ascii",
    "bin",
    "bool",
    "breakpoint",
    "bytearray",
    "bytes",
    "callable",
    "chr",
    "classmethod",
    "compile",
    "complex",
    "delattr",
    "dict",
    "dir",
    "divmod",
    "enumerate",
    "eval",
    "exec",
    "filter",
    "float",
    "format",
    "frozenset",
    "getattr",
    "hasattr",
    "hash",
    "help",
    "hex",
    "id",
    "input",
    "int",
    "isinstance",
    "issubclass",
    "iter",
    "len",
    "list",
    "locals",
    "map",
    "max",
    "memoryview",
    "min",
    "next",
    "object",
    "oct",
    "open",
    "ord",
    "pow",
    "print",
    "property",
    "range",
    "repr",
    "reversed",
    "round",
    "set",
    "setattr",
    "slice",
    "sorted",
    "staticmethod",
    "str",
    "sum",
    "super",
    "tuple",
    "type",
    "vars",
    "zip",
    "__import__",
    "exit",
    "quit",
    "globals",
    "AssertionError",
    "NotImplementedError",
    "BaseException",
    "Exception",
    "ValueError",
    "TypeError",
    "KeyError",
    "IndexError",
    "AttributeError",
    "RuntimeError",
    "StopIteration",
    "FileNotFoundError",
    "IOError",
    "OSError",
    "ImportError",
}

JS_BUILTINS: set[str] = {
    "Boolean",
    "Number",
    "String",
    "Array",
    "Object",
    "Function",
    "Symbol",
    "BigInt",
    "Promise",
    "RegExp",
    "Map",
    "Set",
    "WeakMap",
    "WeakSet",
    "Error",
    "TypeError",
    "RangeError",
    "SyntaxError",
    "ReferenceError",
    "parseInt",
    "parseFloat",
    "isNaN",
    "isFinite",
    "encodeURI",
    "decodeURI",
    "encodeURIComponent",
    "decodeURIComponent",
    "fetch",
    "setTimeout",
    "clearTimeout",
    "setInterval",
    "clearInterval",
    "queueMicrotask",
    "structuredClone",
    "atob",
    "btoa",
    "alert",
    "prompt",
    "confirm",
}

GO_BUILTINS: set[str] = {
    "append",
    "cap",
    "clear",
    "close",
    "complex",
    "copy",
    "delete",
    "imag",
    "len",
    "make",
    "max",
    "min",
    "new",
    "panic",
    "print",
    "println",
    "real",
    "recover",
}


class LanguageParser(ABC):
    def __init__(self, language_name: str, resolver: ModuleResolver | None = None):
        self.language_name = language_name
        self.language: Language = get_language(language_name)
        self.parser: Parser = get_parser(language_name)
        self.resolver = resolver
        self.builtins: set[str] = set()
        if language_name == "python":
            self.builtins = PYTHON_BUILTINS
        elif language_name in ("javascript", "typescript", "tsx"):
            self.builtins = JS_BUILTINS
        elif language_name == "go":
            self.builtins = GO_BUILTINS

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

    def _is_builtin(self, called_name: str, ctx: ParserContext) -> bool:
        """Check if a bare called name is an unresolvable language builtin."""
        if "." in called_name:
            return False
        if called_name not in self.builtins:
            return False
        if called_name in ctx.imported_symbols:
            return False
        if called_name in ctx.defined_symbols:
            return False
        if any(n.name == called_name and n.file_path == ctx.file_path for n in ctx.nodes):
            return False
        return True

    def _resolve_call_target(self, called_name: str, ctx: ParserContext) -> str | None:
        """Resolve a called symbol name to its target node ID (intra-file or cross-file)."""
        # 1. Method calls on self / this inside an enclosing class scope
        if called_name.startswith(("self.", "this.")) and ctx.scope_stack:
            parts = called_name.split(".")
            if len(parts) > 2:
                field_prefix = ".".join(parts[:2])
                bare_field = parts[1]
                member = parts[2]
                class_type = ctx.local_types.get(field_prefix) or ctx.local_types.get(bare_field)
                if class_type:
                    if class_type in self.builtins and class_type not in ctx.imported_symbols:
                        return None
                    if class_type in ctx.imported_symbols:
                        target_file, target_sym = ctx.imported_symbols[class_type]
                        if target_sym == "__module__":
                            return self._get_id(target_file, member)
                        return self._get_id(target_file, f"{target_sym}.{member}")
                    if "." in class_type:
                        c_prefix, c_cls = class_type.rsplit(".", 1)
                        if c_prefix in ctx.imported_symbols:
                            target_file, _ = ctx.imported_symbols[c_prefix]
                            return self._get_id(target_file, f"{c_cls}.{member}")
                    return self._get_id(ctx.file_path, f"{class_type}.{member}")

            member = parts[1]
            enclosing = (
                ".".join(ctx.scope_stack[:-1]) if len(ctx.scope_stack) > 1 else ctx.scope_stack[0]
            )
            return self._get_id(ctx.file_path, f"{enclosing}.{member}")

        # 2. Direct imported symbol call: e.g. `from b import helper` -> `helper()`
        if called_name in ctx.imported_symbols:
            target_file, target_sym = ctx.imported_symbols[called_name]
            return self._get_id(target_file, target_sym)

        # 3. Qualified call on instance variable, imported module or class: e.g. `db.upsert_node()`
        if "." in called_name:
            prefix, rest = called_name.split(".", 1)
            member = rest.split(".")[0]

            # Check if prefix is an instance variable tracked in local_types
            if prefix in ctx.local_types:
                class_type = ctx.local_types[prefix]
                if class_type in self.builtins and class_type not in ctx.imported_symbols:
                    return None
                if class_type in ctx.imported_symbols:
                    target_file, target_sym = ctx.imported_symbols[class_type]
                    if target_sym == "__module__":
                        return self._get_id(target_file, member)
                    return self._get_id(target_file, f"{target_sym}.{member}")
                if "." in class_type:
                    c_prefix, c_cls = class_type.rsplit(".", 1)
                    if c_prefix in ctx.imported_symbols:
                        target_file, _ = ctx.imported_symbols[c_prefix]
                        return self._get_id(target_file, f"{c_cls}.{member}")
                return self._get_id(ctx.file_path, f"{class_type}.{member}")

            if prefix in ctx.imported_symbols:
                target_file, target_sym = ctx.imported_symbols[prefix]
                if target_sym == "__module__":
                    return self._get_id(target_file, member)
                return self._get_id(target_file, f"{target_sym}.{member}")

        # 4. Builtin filtering: unresolvable bare call to standard language builtin
        if self._is_builtin(called_name, ctx):
            return None

        # 5. Default: symbol in the current file
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
