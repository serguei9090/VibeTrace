"""AST-based Duplicated String Literal Detector for Python (Sonar RSPEC-1192).

Detects string literals repeated multiple times within a module or file that should
be extracted into shared constants to prevent typos and ease maintenance.
"""

from __future__ import annotations

import ast
import hashlib
from collections import defaultdict
from typing import TYPE_CHECKING

from vibetrace.quality.models import QualityCategory, QualityFinding, QualitySeverity

if TYPE_CHECKING:
    pass

_DEFAULT_DUPLICATION_THRESHOLD = 3
_MIN_LITERAL_LENGTH = 5

# Common trivial strings that should not trigger duplicate constant warnings
_IGNORED_LITERALS: set[str] = {
    "",
    "utf-8",
    "utf8",
    "ascii",
    "latin-1",
    "__main__",
    "__init__",
    "__all__",
    "true",
    "false",
    "none",
    "null",
    "get",
    "post",
    "put",
    "delete",
    "patch",
    "head",
    "options",
    "text/plain",
    "application/json",
    "description",
    "severity",
    "category",
}


class StringLiteralVisitor(ast.NodeVisitor):
    """AST visitor collecting non-trivial string literal locations."""

    def __init__(self) -> None:
        self.literal_occurrences: dict[str, list[int]] = defaultdict(list)
        self._ignored_nodes: set[int] = set()

    def visit_Module(self, node: ast.Module) -> None:
        self._mark_docstring(node.body)
        self.generic_visit(node)

    def visit_ClassDef(self, node: ast.ClassDef) -> None:
        self._mark_docstring(node.body)
        self.generic_visit(node)

    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
        self._mark_docstring(node.body)
        if node.returns and isinstance(node.returns, ast.Constant):
            self._ignored_nodes.add(id(node.returns))
        for arg in node.args.args + node.args.kwonlyargs:
            if arg.annotation and isinstance(arg.annotation, ast.Constant):
                self._ignored_nodes.add(id(arg.annotation))
        self.generic_visit(node)

    def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> None:
        self._mark_docstring(node.body)
        if node.returns and isinstance(node.returns, ast.Constant):
            self._ignored_nodes.add(id(node.returns))
        for arg in node.args.args + node.args.kwonlyargs:
            if arg.annotation and isinstance(arg.annotation, ast.Constant):
                self._ignored_nodes.add(id(arg.annotation))
        self.generic_visit(node)

    def visit_Dict(self, node: ast.Dict) -> None:
        """Ignores string literal keys within dictionary literals."""
        for k in node.keys:
            if k is not None and isinstance(k, ast.Constant) and isinstance(k.value, str):
                self._ignored_nodes.add(id(k))
        self.generic_visit(node)

    def visit_Assign(self, node: ast.Assign) -> None:
        """Ignores string literals inside __all__ export lists."""
        is_all_export = any(
            isinstance(target, ast.Name) and target.id == "__all__" for target in node.targets
        )
        if is_all_export and isinstance(node.value, (ast.List, ast.Tuple, ast.Set)):
            for elt in node.value.elts:
                if isinstance(elt, ast.Constant):
                    self._ignored_nodes.add(id(elt))
        self.generic_visit(node)

    def visit_AnnAssign(self, node: ast.AnnAssign) -> None:
        """Ignores string type annotations."""
        if node.annotation and isinstance(node.annotation, ast.Constant):
            self._ignored_nodes.add(id(node.annotation))
        self.generic_visit(node)

    def _mark_docstring(self, body: list[ast.stmt]) -> None:
        """Excludes leading docstring statements from duplicate collection."""
        if body and isinstance(body[0], ast.Expr):
            val = body[0].value
            if isinstance(val, ast.Constant) and isinstance(val.value, str):
                self._ignored_nodes.add(id(val))

    def visit_Constant(self, node: ast.Constant) -> None:
        if id(node) in self._ignored_nodes:
            return
        if not isinstance(node.value, str):
            return

        text = node.value.strip()
        if len(text) < _MIN_LITERAL_LENGTH:
            return
        if text.lower() in _IGNORED_LITERALS:
            return
        # Ignore strings that look like regexes, format directives, or single words with whitespace
        if text.startswith(("\\", "^", "@", "$")) or "\n" in text:
            return

        self.literal_occurrences[node.value].append(node.lineno)


def analyze_literal_duplications(
    file_path: str,
    source_code: str,
    threshold: int = _DEFAULT_DUPLICATION_THRESHOLD,
) -> list[QualityFinding]:
    """Analyzes a Python file for string literals duplicated beyond the threshold (RSPEC-1192)."""
    try:
        tree = ast.parse(source_code, filename=file_path)
    except SyntaxError:
        return []

    visitor = StringLiteralVisitor()
    visitor.visit(tree)

    findings: list[QualityFinding] = []
    source_lines = source_code.splitlines()

    for literal, lines in sorted(visitor.literal_occurrences.items(), key=lambda x: -len(x[1])):
        count = len(lines)
        if count < threshold:
            continue

        first_line = lines[0]
        hash_id = hashlib.sha256(f"{file_path}:{literal}".encode()).hexdigest()[:12]
        line_preview = source_lines[first_line - 1] if 0 < first_line <= len(source_lines) else ""
        escaped_literal = literal[:40] + ("..." if len(literal) > 40 else "")

        findings.append(
            QualityFinding(
                id=f"QUAL-S1192-{hash_id}",
                rule_id="python:S1192",
                rspec_id="RSPEC-1192",
                category=QualityCategory.CODE_SMELL,
                severity=QualitySeverity.LOW,
                title=(
                    f"Define a constant instead of duplicating this literal "
                    f"'{escaped_literal}' {count} times."
                ),
                message=(
                    f"String literal '{escaped_literal}' is duplicated {count} times "
                    f"at lines {', '.join(map(str, lines[:5]))}."
                ),
                file_path=file_path,
                start_line=first_line,
                end_line=first_line,
                symbol_name=None,
                code_hash=hashlib.sha256(f"{literal}:{count}".encode()).hexdigest(),
                non_compliant_snippet=line_preview.strip() or f'"{escaped_literal}"',
                compliant_snippet=f'CONSTANT_NAME = "{escaped_literal}"',
                remediation_guidance=(
                    "Define a module-level constant (e.g. UPPER_CASE_NAME) for this string "
                    "literal and reference the constant across all occurrences."
                ),
            )
        )

    return findings
