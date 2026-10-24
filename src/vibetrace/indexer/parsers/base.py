import hashlib
from abc import ABC, abstractmethod
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from tree_sitter import Language, Node, Parser, Tree

from vibetrace.indexer.db import Edge
from vibetrace.indexer.db import Node as DBNode
from vibetrace.indexer.parsers.builtins import (
    CPP_BUILTINS,
    CSHARP_BUILTINS,
    GO_BUILTINS,
    JAVA_BUILTINS,
    JS_BUILTINS,
    JS_GLOBALS,
    PHP_BUILTINS,
    PYTHON_BUILTINS,
    RUBY_BUILTINS,
    RUST_BUILTINS,
)
from vibetrace.indexer.resolver import ModuleResolver

__all__ = [
    "CPP_BUILTINS",
    "CSHARP_BUILTINS",
    "GO_BUILTINS",
    "JAVA_BUILTINS",
    "JS_BUILTINS",
    "JS_GLOBALS",
    "PHP_BUILTINS",
    "PYTHON_BUILTINS",
    "RUBY_BUILTINS",
    "RUST_BUILTINS",
    "LanguageParser",
    "ParserContext",
    "get_language",
    "get_parser",
]


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


_LANGUAGE_FACTORIES: dict[str, Callable[[], Any]] = {
    "python": lambda: __import__("tree_sitter_python").language(),
    "javascript": lambda: __import__("tree_sitter_javascript").language(),
    "typescript": lambda: __import__("tree_sitter_typescript").language_typescript(),
    "tsx": lambda: __import__("tree_sitter_typescript").language_tsx(),
    "go": lambda: __import__("tree_sitter_go").language(),
    "rust": lambda: __import__("tree_sitter_rust").language(),
    "java": lambda: __import__("tree_sitter_java").language(),
    "c": lambda: __import__("tree_sitter_c").language(),
    "cpp": lambda: __import__("tree_sitter_cpp").language(),
    "c_sharp": lambda: __import__("tree_sitter_c_sharp").language(),
    "ruby": lambda: __import__("tree_sitter_ruby").language(),
    "php": lambda: __import__("tree_sitter_php").language_php(),
}

_LANGUAGES_CACHE: dict[str, Language] = {}


def get_language(lang_name: str) -> Language:
    """Retrieve or initialize a cached official tree-sitter Language instance."""
    if lang_name not in _LANGUAGES_CACHE:
        factory = _LANGUAGE_FACTORIES.get(lang_name)
        if not factory:
            raise ValueError(f"Unsupported tree-sitter language: {lang_name}")
        _LANGUAGES_CACHE[lang_name] = Language(factory())
    return _LANGUAGES_CACHE[lang_name]


def get_parser(lang_name: str) -> Parser:
    """Instantiate a tree-sitter Parser configured with the target language."""
    return Parser(get_language(lang_name))


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

    def _clean_ts_type(self, raw: str) -> str:
        clean = raw.lstrip(": ").strip()
        parts = [
            p.strip() for p in clean.split("|") if p.strip() not in ("null", "undefined", "void")
        ]
        return parts[0] if parts else clean

    def _record_ts_val_type(self, target_name: str, val_node: Node, ctx: ParserContext) -> None:
        curr: Node | None = val_node
        while curr:
            if curr.type in ("parenthesized_expression", "non_null_expression"):
                curr = curr.child_by_field_name("expression") or (
                    curr.children[0] if curr.children else None
                )
            elif curr.type == "as_expression":
                t_node = (
                    curr.children[2] if len(curr.children) > 2 else curr.child_by_field_name("type")
                )
                if t_node:
                    clean = self._clean_ts_type(self._get_text(t_node))
                    if clean and clean not in ("any", "unknown"):
                        ctx.local_types[target_name] = clean
                        return
                curr = curr.children[0] if curr.children else None
            elif curr.type == "type_assertion":
                t_node = (
                    curr.children[1] if len(curr.children) > 1 else curr.child_by_field_name("type")
                )
                if t_node:
                    clean = self._clean_ts_type(self._get_text(t_node))
                    if clean and clean not in ("any", "unknown"):
                        ctx.local_types[target_name] = clean
                        return
                curr = curr.children[-1] if curr.children else None
            else:
                break

        if not curr:
            return

        if curr.type == "new_expression" and (ctor := curr.child_by_field_name("constructor")):
            ctx.local_types[target_name] = self._get_text(ctor).split("<")[0].strip()
        elif (
            curr.type == "identifier"
            and (r_name := self._get_text(curr).strip()) in ctx.local_types
        ):
            ctx.local_types[target_name] = ctx.local_types[r_name]

    def _get_text(self, node: Node | None) -> str:
        """Extract text from a tree-sitter node."""
        if node is None or node.text is None:
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
        # 1. Method calls on self / this / Self inside an enclosing class scope
        if called_name.startswith(("self.", "this.", "Self.", "cls.")) and ctx.scope_stack:
            parts = called_name.split(".")
            if len(parts) > 2:
                field_prefix = ".".join(parts[:2])
                bare_field = parts[1]
                member = ".".join(parts[2:])
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

            member = ".".join(parts[1:])
            class_enclosing = None
            for i in range(len(ctx.scope_stack) - 1, -1, -1):
                candidate_scope = ".".join(ctx.scope_stack[: i + 1])
                for n in ctx.nodes:
                    if n.file_path == ctx.file_path and n.kind in (
                        "class",
                        "struct",
                        "interface",
                        "trait",
                    ):
                        if n.qualified_name == candidate_scope or n.name == candidate_scope:
                            class_enclosing = candidate_scope
                            break
                if class_enclosing:
                    break

            if not class_enclosing:
                class_enclosing = ctx.scope_stack[0]

            return self._get_id(ctx.file_path, f"{class_enclosing}.{member}")

        # 2. Direct imported symbol call: e.g. `from b import helper` -> `helper()`
        if called_name in ctx.imported_symbols:
            target_file, target_sym = ctx.imported_symbols[called_name]
            return self._get_id(target_file, target_sym)

        # 3. Qualified call on instance variable, imported module or class: e.g. `db.upsert_node()`
        if "." in called_name:
            prefix, rest = called_name.split(".", 1)
            lookup_prefix = (
                "super()" if prefix.startswith("super(") and prefix.endswith(")") else prefix
            )

            # Check if prefix is an ambient global or language builtin class:
            # e.g. console.log(), Promise.resolve(), System.out.println(), std.cout
            if (
                (
                    prefix in self.builtins
                    or (
                        self.language_name in ("javascript", "typescript", "tsx")
                        and prefix in JS_GLOBALS
                    )
                )
                and prefix not in ctx.imported_symbols
                and prefix not in ctx.local_types
                and prefix not in ctx.defined_symbols
            ):
                return None

            # Check if prefix is an instance variable tracked in local_types
            if lookup_prefix in ctx.local_types:
                class_type = ctx.local_types[lookup_prefix]
                if class_type in self.builtins and class_type not in ctx.imported_symbols:
                    return None
                if class_type in ctx.imported_symbols:
                    target_file, target_sym = ctx.imported_symbols[class_type]
                    sym = rest if target_sym == "__module__" else f"{target_sym}.{rest}"
                    return self._get_id(target_file, sym)
                if "." in class_type:
                    c_prefix, c_cls = class_type.rsplit(".", 1)
                    if c_prefix in ctx.imported_symbols:
                        target_file, _ = ctx.imported_symbols[c_prefix]
                        return self._get_id(target_file, f"{c_cls}.{rest}")
                return self._get_id(ctx.file_path, f"{class_type}.{rest}")

            if prefix in ctx.imported_symbols:
                target_file, target_sym = ctx.imported_symbols[prefix]
                sym = rest if target_sym == "__module__" else f"{target_sym}.{rest}"
                return self._get_id(target_file, sym)

        # 4. Builtin filtering: unresolvable bare call to standard language builtin
        if self._is_builtin(called_name, ctx):
            return None

        # 5. Wildcard import resolution: e.g. from utils import * -> helper()
        if "*" in ctx.imported_symbols and called_name not in ctx.defined_symbols:
            target_file, _ = ctx.imported_symbols["*"]
            return self._get_id(target_file, called_name)

        # 6. Default: symbol in the current file
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

    def _add_node(
        self,
        ctx: ParserContext,
        node_id: str,
        name: str,
        kind: Any,
        node: Node | None,
        parent_id: str | None = None,
        qualified_name: str | None = None,
        visibility: str | None = None,
        signature: str | None = None,
    ) -> DBNode:
        """Create DBNode, append to ctx.nodes, and emit defines edge if parent_id is present."""
        db_node = self._create_node(
            node_id,
            name,
            kind,
            ctx.file_path,
            node,
            signature=signature,
            visibility=visibility,
            qualified_name=qualified_name,
            parent_id=parent_id,
        )
        ctx.nodes.append(db_node)
        if parent_id:
            ctx.edges.append(self._create_edge(parent_id, node_id, "defines", node=node))
        return db_node

    def _register_imported_module(
        self, module_spec: str, ctx: ParserContext, node: Node
    ) -> str | None:
        """Register an imported module node and import edge, returning resolved file path."""
        res = self.resolver.resolve(module_spec, ctx.file_path) if self.resolver else None
        if res == ctx.file_path:
            return res

        m_path = res or "external"
        c_mod = (
            self.resolver.get_module_name(res) if res and self.resolver else None
        ) or module_spec
        m_id = self._get_id(m_path, c_mod, kind="module")
        if not any(n.id == m_id for n in ctx.nodes):
            ctx.nodes.append(self._create_node(m_id, c_mod, "module", m_path, node))
        ctx.edges.append(self._create_edge(ctx.file_module_id, m_id, "imports", node=node))
        return res

    @abstractmethod
    def extract(self, file_path: str, source_code: bytes) -> tuple[list[DBNode], list[Edge]]:
        """Extract nodes and edges from source code."""
        pass
