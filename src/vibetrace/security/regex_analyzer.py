"""Regular Expression Quality, Complexity, and ReDoS Security Analyzer.

Inspects regular expression literals in re.compile, re.search, and re.match for:
- python:S6353 / RSPEC-6353: Concise character class syntax ([A-Za-z0-9_] -> \\w)
- python:S8786 / CWE-1333: ReDoS super-linear runtime due to catastrophic backtracking
- python:S5843 / RSPEC-5843: Regex complexity exceeding threshold (> 20 operators/branches)
"""

from __future__ import annotations

import ast
import hashlib
import re

from vibetrace.security.models import FindingCategory, SecurityFinding, Severity

# Redundant character class patterns (S6353)
_WORD_CLASS_RE = re.compile(r"\[(?:A-Za-z0-9_|a-zA-Z0-9_|0-9A-Za-z_|0-9a-zA-Z_)\]")
_DIGIT_CLASS_RE = re.compile(r"\[0-9\]")
_SPACE_CLASS_RE = re.compile(r"\[[ \t\r\n\v\f]+\]")

# ReDoS catastrophic backtracking patterns (CWE-1333 / S8786)
# 1. Nested quantifiers: (a+)+, ([a-z]*)*, (\s+)+
_NESTED_QUANT_RE = re.compile(r"\([^)]*[\*\+\?][^)]*\)[\*\+]")
# 2. Overlapping adjacent unbounded quantifiers: \s* followed by optional group followed by \s*
_OVERLAPPING_QUANT_RE = re.compile(r"\\s[\*\+]\s*(?:\([^)]*\)\?|\([^\)]*\)\*)\s*\\s[\*\+]")


class RegexQualityAnalyzer(ast.NodeVisitor):
    """AST visitor extracting string arguments from regex invocation calls."""

    def __init__(self, file_path: str, source_lines: list[str]) -> None:
        self.file_path = file_path.replace("\\", "/")
        self.source_lines = source_lines
        self.findings: list[SecurityFinding] = []

    def _hash_finding(self, msg: str, line: int) -> str:
        raw = f"{self.file_path}:{line}:{msg}"
        return hashlib.sha256(raw.encode("utf-8")).hexdigest()

    def _calculate_regex_complexity(self, pattern: str) -> int:
        """Estimates structural complexity based on operators, groups, and alternations."""
        complexity = 0
        complexity += pattern.count("|") * 2
        complexity += pattern.count("(")
        complexity += pattern.count("[")
        complexity += pattern.count("*")
        complexity += pattern.count("+")
        complexity += pattern.count("?")
        return complexity

    def _audit_regex_pattern(self, pattern: str, line_no: int) -> None:
        """Evaluates regex string for S6353, CWE-1333 (S8786), and S5843."""
        # 1. S6353: Concise character class syntax
        if _WORD_CLASS_RE.search(pattern):
            hash_id = hashlib.sha256(f"{self.file_path}:{line_no}:word_class".encode()).hexdigest()[
                :12
            ]
            self.findings.append(
                SecurityFinding(
                    id=f"SEC-S6353-{hash_id}",
                    rule_id="python:S6353",
                    cwe_id="CWE-1078",
                    category=FindingCategory.AST_PATTERN,
                    severity=Severity.LOW,
                    title="Use concise character class syntax '\\w' instead of '[A-Za-z0-9_]'.",
                    message="Use concise character class syntax '\\w' instead of '[A-Za-z0-9_]'.",
                    file_path=self.file_path,
                    start_line=line_no,
                    end_line=line_no,
                    symbol_name=None,
                    code_hash=self._hash_finding("word_class", line_no),
                    non_compliant_snippet=pattern[:100],
                    compliant_snippet=r"Use \w in place of [A-Za-z0-9_]",
                    remediation_guidance=(
                        "Replace verbose range '[A-Za-z0-9_]' with character class '\\w'."
                    ),
                )
            )

        if _DIGIT_CLASS_RE.search(pattern):
            hash_id = hashlib.sha256(
                f"{self.file_path}:{line_no}:digit_class".encode()
            ).hexdigest()[:12]
            self.findings.append(
                SecurityFinding(
                    id=f"SEC-S6353-{hash_id}",
                    rule_id="python:S6353",
                    cwe_id="CWE-1078",
                    category=FindingCategory.AST_PATTERN,
                    severity=Severity.LOW,
                    title="Use concise character class syntax '\\d' instead of '[0-9]'.",
                    message="Use concise character class syntax '\\d' instead of '[0-9]'.",
                    file_path=self.file_path,
                    start_line=line_no,
                    end_line=line_no,
                    symbol_name=None,
                    code_hash=self._hash_finding("digit_class", line_no),
                    non_compliant_snippet=pattern[:100],
                    compliant_snippet=r"Use \d in place of [0-9]",
                    remediation_guidance=(
                        "Replace verbose range '[0-9]' with character class '\\d'."
                    ),
                )
            )

        # 2. S8786 / CWE-1333: ReDoS catastrophic backtracking
        if _NESTED_QUANT_RE.search(pattern) or _OVERLAPPING_QUANT_RE.search(pattern):
            hash_id = hashlib.sha256(f"{self.file_path}:{line_no}:redos".encode()).hexdigest()[:12]
            self.findings.append(
                SecurityFinding(
                    id=f"SEC-CWE-1333-{hash_id}",
                    rule_id="python:S8786",
                    cwe_id="CWE-1333",
                    category=FindingCategory.AST_PATTERN,
                    severity=Severity.HIGH,
                    title=(
                        "Simplify regular expression to reduce runtime: "
                        "super-linear performance due to backtracking."
                    ),
                    message=(
                        "Catastrophic backtracking vulnerability detected (ReDoS). "
                        "Overlapping repeated quantifiers cause exponential runtime."
                    ),
                    file_path=self.file_path,
                    start_line=line_no,
                    end_line=line_no,
                    symbol_name=None,
                    code_hash=self._hash_finding("redos", line_no),
                    non_compliant_snippet=pattern[:100],
                    compliant_snippet=None,
                    remediation_guidance="Refactor overlapping quantifiers to prevent ReDoS.",
                )
            )

        # 3. S5843: Regex complexity exceeding threshold (> 20)
        complexity = self._calculate_regex_complexity(pattern)
        if complexity > 20:
            hash_id = hashlib.sha256(f"{self.file_path}:{line_no}:complexity".encode()).hexdigest()[
                :12
            ]
            self.findings.append(
                SecurityFinding(
                    id=f"SEC-S5843-{hash_id}",
                    rule_id="python:S5843",
                    cwe_id="CWE-1078",
                    category=FindingCategory.AST_PATTERN,
                    severity=Severity.MEDIUM,
                    title=(
                        f"Simplify regular expression to reduce complexity from {complexity} to 20."
                    ),
                    message=f"Regular expression complexity ({complexity}) exceeds allowed (20).",
                    file_path=self.file_path,
                    start_line=line_no,
                    end_line=line_no,
                    symbol_name=None,
                    code_hash=self._hash_finding("regex_complexity", line_no),
                    non_compliant_snippet=pattern[:100],
                    compliant_snippet=None,
                    remediation_guidance=(
                        "Break down complex monolithic regex into smaller sub-patterns."
                    ),
                )
            )

    def visit_Call(self, node: ast.Call) -> None:
        """Inspects function calls to re.compile, re.search, re.match, and re.findall."""
        func_name = ""
        re_methods = ("compile", "search", "match", "findall", "finditer", "sub")
        if isinstance(node.func, ast.Attribute) and isinstance(node.func.value, ast.Name):
            if node.func.value.id == "re" and node.func.attr in re_methods:
                func_name = f"re.{node.func.attr}"
        elif isinstance(node.func, ast.Name) and node.func.id in ("compile", "search", "match"):
            func_name = node.func.id

        if func_name and node.args:
            first_arg = node.args[0]
            if isinstance(first_arg, ast.Constant) and isinstance(first_arg.value, str):
                self._audit_regex_pattern(first_arg.value, first_arg.lineno)

        self.generic_visit(node)


def analyze_regex_quality(file_path: str, source_code: str) -> list[SecurityFinding]:
    """Analyzes a Python source file for regex clean-code, ReDoS, and complexity smells."""
    try:
        tree = ast.parse(source_code, filename=file_path)
    except SyntaxError:
        return []

    source_lines = source_code.splitlines()
    visitor = RegexQualityAnalyzer(file_path=file_path, source_lines=source_lines)
    visitor.visit(tree)
    return visitor.findings
