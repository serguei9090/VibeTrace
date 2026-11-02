"""Cognitive and Cyclomatic Complexity evaluation via AST analysis."""

from __future__ import annotations

import ast
import hashlib
from dataclasses import dataclass

from vibetrace.quality.models import (
    QualityCategory,
    QualityFinding,
    QualitySeverity,
)
from vibetrace.quality.rules import QUALITY_RULES_CATALOG


@dataclass
class FunctionComplexity:
    """Calculated complexity metrics for a single function or method."""

    name: str
    start_line: int
    end_line: int
    cognitive_complexity: int
    cyclomatic_complexity: int
    code_hash: str
    snippet: str


class PythonCognitiveVisitor(ast.NodeVisitor):
    """Calculates Cognitive Complexity according to SonarSource formal specification.

    Rules:
    - Increments (+1) for breaks in linear flow: if, for, while, except, match_case, ternary.
    - Nesting penalty (+1 per nesting level) applied to control flow structures.
    - Nesting level increases inside nested functions, lambdas, and control flow blocks.
    - Boolean operator changes in boolean expressions increment by 1.
    """

    def __init__(self) -> None:
        self.cognitive_score: int = 0
        self.cyclomatic_score: int = 1  # Base cyclomatic complexity is 1
        self._nesting: int = 0

    def _increment(self, structural: bool = True) -> None:
        if structural:
            self.cognitive_score += 1 + self._nesting
        else:
            self.cognitive_score += 1
        self.cyclomatic_score += 1

    def visit_If(self, node: ast.If) -> None:
        self._increment(structural=True)
        self._nesting += 1
        for child in node.body:
            self.visit(child)
        self._nesting -= 1

        # Check if orelse is an elif vs plain else
        if node.orelse:
            if len(node.orelse) == 1 and isinstance(node.orelse[0], ast.If):
                # Elif gets +1 structural increment without nesting penalty increment
                self.cognitive_score += 1
                self.cyclomatic_score += 1
                # Continue visiting elif body with same nesting
                self._nesting += 1
                for child in node.orelse[0].body:
                    self.visit(child)
                self._nesting -= 1
                if node.orelse[0].orelse:
                    # Recursive tail for further elif/else
                    fake_if = node.orelse[0]
                    node.orelse = fake_if.orelse
                    self.visit_If(fake_if)
            else:
                self.cognitive_score += 1  # else adds 1 cognitive
                self._nesting += 1
                for child in node.orelse:
                    self.visit(child)
                self._nesting -= 1

    def visit_IfExp(self, node: ast.IfExp) -> None:
        # Ternary conditional expression
        self._increment(structural=True)
        self.generic_visit(node)

    def _visit_loop(self, node: ast.For | ast.AsyncFor | ast.While) -> None:
        self._increment(structural=True)
        self._nesting += 1
        for child in node.body:
            self.visit(child)
        self._nesting -= 1
        for child in node.orelse:
            self.visit(child)

    def visit_For(self, node: ast.For) -> None:
        self._visit_loop(node)

    def visit_AsyncFor(self, node: ast.AsyncFor) -> None:
        self._visit_loop(node)

    def visit_While(self, node: ast.While) -> None:
        self._visit_loop(node)

    def _visit_guarded_scope(self, node: ast.AST) -> None:
        self._increment(structural=True)
        self._nesting += 1
        self.generic_visit(node)
        self._nesting -= 1

    def visit_ExceptHandler(self, node: ast.ExceptHandler) -> None:
        self._visit_guarded_scope(node)

    def visit_Match(self, node: ast.Match) -> None:
        # Python 3.10+ pattern matching
        self._visit_guarded_scope(node)

    def visit_BoolOp(self, node: ast.BoolOp) -> None:
        # Sequences of mixed boolean operators (e.g. a and b or c)
        self.cognitive_score += len(node.values) - 1
        self.cyclomatic_score += len(node.values) - 1
        self.generic_visit(node)

    def _visit_function_scope(self, node: ast.FunctionDef | ast.AsyncFunctionDef) -> None:
        self._nesting += 1
        for stmt in node.body:
            self.visit(stmt)
        self._nesting -= 1

    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
        # Nested functions increase the nesting level for code inside them
        self._visit_function_scope(node)

    def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> None:
        self._visit_function_scope(node)

    def visit_Lambda(self, node: ast.Lambda) -> None:
        self._nesting += 1
        self.generic_visit(node)
        self._nesting -= 1


def _measure_function_complexity(
    node: ast.FunctionDef | ast.AsyncFunctionDef,
    source_lines: list[str],
) -> FunctionComplexity:
    """Measures cognitive and cyclomatic complexity metrics for a function AST node."""
    visitor = PythonCognitiveVisitor()
    for stmt in node.body:
        visitor.visit(stmt)

    start_line = node.lineno
    end_line = getattr(node, "end_lineno", start_line)
    func_snippet = "\n".join(source_lines[start_line - 1 : end_line])
    code_hash = hashlib.sha256(func_snippet.strip().encode("utf-8")).hexdigest()

    return FunctionComplexity(
        name=node.name,
        start_line=start_line,
        end_line=end_line,
        cognitive_complexity=visitor.cognitive_score,
        cyclomatic_complexity=visitor.cyclomatic_score,
        code_hash=code_hash,
        snippet=func_snippet[:300] + ("..." if len(func_snippet) > 300 else ""),
    )


def _build_complexity_finding(
    fc: FunctionComplexity,
    file_path: str,
    threshold: int,
) -> QualityFinding:
    """Emits an S3776 finding for a function exceeding cognitive complexity threshold."""
    spec = QUALITY_RULES_CATALOG.get("S3776")
    raw_id = f"{file_path}:{fc.name}:{fc.code_hash[:16]}"
    fid_hash = hashlib.sha256(raw_id.encode()).hexdigest()[:12]
    finding_id = f"QUAL-S3776-{fid_hash}"

    return QualityFinding(
        id=finding_id,
        rule_id="python:S3776",
        rspec_id="RSPEC-3776",
        category=QualityCategory.COMPLEXITY,
        severity=QualitySeverity.HIGH,
        title=(
            f"Refactor '{fc.name}' to reduce its Cognitive Complexity from "
            f"{fc.cognitive_complexity} to the {threshold} allowed."
        ),
        message=(
            f"Function '{fc.name}' has Cognitive Complexity of "
            f"{fc.cognitive_complexity} (maximum allowed is {threshold})."
        ),
        file_path=file_path.replace("\\", "/"),
        start_line=fc.start_line,
        end_line=fc.end_line,
        symbol_name=fc.name,
        code_hash=fc.code_hash,
        metric_value=float(fc.cognitive_complexity),
        metric_threshold=float(threshold),
        non_compliant_snippet=fc.snippet,
        compliant_snippet=spec.compliant_example if spec else None,
        remediation_guidance=spec.explanation if spec else None,
        status="UNAPPROVED",
    )


def analyze_python_complexities(
    file_path: str,
    source_code: str,
    threshold: int = 15,
) -> tuple[list[FunctionComplexity], list[QualityFinding]]:
    """Analyzes a Python source file for Cognitive and Cyclomatic complexity."""
    try:
        tree = ast.parse(source_code, filename=file_path)
    except SyntaxError:
        return [], []

    source_lines = source_code.splitlines()
    complexities: list[FunctionComplexity] = []
    findings: list[QualityFinding] = []

    spec = QUALITY_RULES_CATALOG.get("S3776")
    default_t = spec.default_threshold if spec and spec.default_threshold is not None else 15
    effective_threshold: int = threshold if threshold > 0 else default_t

    for node in ast.walk(tree):
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue

        fc = _measure_function_complexity(node, source_lines)
        complexities.append(fc)

        if fc.cognitive_complexity > effective_threshold:
            findings.append(_build_complexity_finding(fc, file_path, effective_threshold))

    return complexities, findings
