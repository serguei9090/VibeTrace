"""Surgical AST Codemods for Deterministic Code Smells.

Automates deterministic refactoring for:
- RSPEC-7503: Replace async def with def when function contains no await expressions.
- RSPEC-1066: Merge collapsible nested if statements.
"""

from __future__ import annotations

import ast
import re
from pathlib import Path


class QualityCodemodApplier:
    """Applies surgical AST transformations to fix deterministic quality smells."""

    @staticmethod
    def fix_s7503_async_def(source_code: str) -> tuple[str, int]:
        """Converts async def to def if function has no await and is not special."""
        try:
            tree = ast.parse(source_code)
        except SyntaxError:
            return source_code, 0

        lines = source_code.splitlines(keepends=True)
        fixes = 0

        for node in ast.walk(tree):
            if not isinstance(node, ast.AsyncFunctionDef):
                continue
            if node.name in ("__aenter__", "__aexit__"):
                continue

            # Check if any decorators indicate framework async handlers
            is_framework_handler = False
            for d in node.decorator_list:
                d_repr = ast.unparse(d) if hasattr(ast, "unparse") else ""
                if any(
                    x in d_repr
                    for x in (
                        "route",
                        "get",
                        "post",
                        "put",
                        "delete",
                        "websocket",
                        "abstractmethod",
                    )
                ):
                    is_framework_handler = True
                    break
            if is_framework_handler:
                continue

            # Check for await inside function body (excluding nested functions)
            has_await = False
            for child in ast.walk(node):
                if child is node:
                    continue
                if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    continue
                if isinstance(child, ast.Await):
                    has_await = True
                    break

            if not has_await:
                line_idx = node.lineno - 1
                if 0 <= line_idx < len(lines):
                    line = lines[line_idx]
                    new_line = re.sub(r"\basync\s+def\b", "def", line, count=1)
                    if new_line != line:
                        lines[line_idx] = new_line
                        fixes += 1

        if fixes > 0:
            return "".join(lines), fixes
        return source_code, 0

    @staticmethod
    def fix_s1066_nested_ifs(source_code: str) -> tuple[str, int]:
        """Merges nested if statements with no else branches."""
        try:
            tree = ast.parse(source_code)
        except SyntaxError:
            return source_code, 0

        class MergeIfTransformer(ast.NodeTransformer):
            def __init__(self) -> None:
                super().__init__()
                self.count = 0

            def visit_If(self, node: ast.If) -> ast.AST:
                self.generic_visit(node)
                if (
                    len(node.body) == 1
                    and isinstance(node.body[0], ast.If)
                    and not node.orelse
                    and not node.body[0].orelse
                ):
                    inner = node.body[0]
                    new_test = ast.BoolOp(op=ast.And(), values=[node.test, inner.test])
                    self.count += 1
                    return ast.copy_location(
                        ast.If(test=new_test, body=inner.body, orelse=[]),
                        node,
                    )
                return node

        transformer = MergeIfTransformer()
        new_tree = transformer.visit(tree)
        if transformer.count > 0:
            ast.fix_missing_locations(new_tree)
            try:
                new_code = ast.unparse(new_tree) + "\n"
                # Validate syntax of generated code before returning
                ast.parse(new_code)
                return new_code, transformer.count
            except Exception:
                return source_code, 0
        return source_code, 0

    @classmethod
    def fix_file(cls, file_path: Path) -> dict[str, int]:
        """Applies all surgical codemods to a given Python file."""
        if file_path.suffix.lower() != ".py":
            return {}
        try:
            source = file_path.read_text(encoding="utf-8")
        except OSError:
            return {}

        results: dict[str, int] = {}

        # 1. S7503: Replace async def with def where safe
        s7503_code, count_7503 = cls.fix_s7503_async_def(source)
        if count_7503 > 0:
            results["python:S7503"] = count_7503
            source = s7503_code

        if results:
            file_path.write_text(source, encoding="utf-8")

        return results
