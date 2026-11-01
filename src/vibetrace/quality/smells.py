"""AST Code Smell detectors for Maintainability & Reliability rules."""

from __future__ import annotations

import ast
import hashlib

from vibetrace.quality.models import (
    QualityCategory,
    QualityFinding,
    QualitySeverity,
)
from vibetrace.quality.rules import QUALITY_RULES_CATALOG


class PythonSmellsVisitor(ast.NodeVisitor):
    """Detects clean code smells and anti-patterns according to RSPEC and Clean Code rules."""

    def __init__(self, file_path: str, source_lines: list[str]) -> None:
        self.file_path = file_path.replace("\\", "/")
        self.source_lines = source_lines
        self.findings: list[QualityFinding] = []
        self._nesting_depth = 0
        self._current_function: str | None = None

    def _get_snippet(self, start_line: int, end_line: int) -> str:
        lines = self.source_lines[start_line - 1 : end_line]
        return "\n".join(lines).strip()

    def _hash_snippet(self, snippet: str) -> str:
        return hashlib.sha256(snippet.encode("utf-8")).hexdigest()

    def _check_block_unreachable(self, stmts: list[ast.stmt]) -> None:
        """S1763 / Google:DeadCode: Detects statements after terminal jumps."""
        spec_1763 = QUALITY_RULES_CATALOG.get("S1763")
        jump_idx = -1
        for i, stmt in enumerate(stmts):
            if isinstance(stmt, (ast.Return, ast.Raise, ast.Break, ast.Continue)):
                jump_idx = i
                break

        if jump_idx != -1 and jump_idx < len(stmts) - 1:
            dead_stmt = stmts[jump_idx + 1]
            end_l = getattr(stmts[-1], "end_lineno", dead_stmt.lineno)
            snippet = self._get_snippet(dead_stmt.lineno, end_l)
            code_hash = self._hash_snippet(snippet)
            raw_id = f"{self.file_path}:{dead_stmt.lineno}:dead_code"
            fid = f"QUAL-S1763-{hashlib.sha256(raw_id.encode()).hexdigest()[:12]}"
            self.findings.append(
                QualityFinding(
                    id=fid,
                    rule_id="python:S1763",
                    rspec_id="RSPEC-1763",
                    category=QualityCategory.CODE_SMELL,
                    severity=QualitySeverity.MEDIUM,
                    title="Statements should not occur after terminal jump statements.",
                    message=(
                        "Unreachable code detected immediately after return, "
                        "raise, break, or continue."
                    ),
                    file_path=self.file_path,
                    start_line=dead_stmt.lineno,
                    end_line=end_l,
                    symbol_name=self._current_function,
                    code_hash=code_hash,
                    non_compliant_snippet=snippet[:200],
                    compliant_snippet=spec_1763.compliant_example if spec_1763 else None,
                    remediation_guidance=spec_1763.explanation if spec_1763 else None,
                )
            )

    def _check_function_param_count(self, node: ast.FunctionDef | ast.AsyncFunctionDef) -> None:
        """S107: Too many parameters (> 7)."""
        spec_107 = QUALITY_RULES_CATALOG.get("S107")
        param_thresh = spec_107.default_threshold if spec_107 and spec_107.default_threshold else 7
        total_args = (
            len(node.args.posonlyargs)
            + len(node.args.args)
            + len(node.args.kwonlyargs)
            + (1 if node.args.vararg else 0)
            + (1 if node.args.kwarg else 0)
        )
        if node.name != "__init__" and total_args > param_thresh:
            snippet = self._get_snippet(node.lineno, getattr(node, "end_lineno", node.lineno))
            code_hash = self._hash_snippet(snippet)
            raw_id = f"{self.file_path}:{node.name}:{node.lineno}"
            fid = f"QUAL-S107-{hashlib.sha256(raw_id.encode()).hexdigest()[:12]}"
            self.findings.append(
                QualityFinding(
                    id=fid,
                    rule_id="python:S107",
                    rspec_id="RSPEC-107",
                    category=QualityCategory.CODE_SMELL,
                    severity=QualitySeverity.LOW,
                    title=(
                        f"Function '{node.name}' has {total_args} parameters "
                        f"(exceeds {param_thresh})."
                    ),
                    message=(
                        f"Refactor '{node.name}' to group related parameters into "
                        "a configuration object or Pydantic model."
                    ),
                    file_path=self.file_path,
                    start_line=node.lineno,
                    end_line=getattr(node, "end_lineno", node.lineno),
                    symbol_name=node.name,
                    code_hash=code_hash,
                    metric_value=float(total_args),
                    metric_threshold=float(param_thresh),
                    non_compliant_snippet=snippet[:200],
                    compliant_snippet=spec_107.compliant_example if spec_107 else None,
                    remediation_guidance=spec_107.explanation if spec_107 else None,
                )
            )

    def _check_mutable_defaults(self, node: ast.FunctionDef | ast.AsyncFunctionDef) -> None:
        """S5714 / B006: Mutable default arguments."""
        all_defaults = list(node.args.defaults) + [d for d in node.args.kw_defaults if d]
        for d in all_defaults:
            if isinstance(d, (ast.List, ast.Dict, ast.Set)):
                spec_5714 = QUALITY_RULES_CATALOG.get("S5714")
                snippet = self._get_snippet(d.lineno, getattr(d, "end_lineno", d.lineno))
                code_hash = self._hash_snippet(snippet)
                raw_id = f"{self.file_path}:{node.name}:{d.lineno}:mutable_default"
                fid = f"QUAL-S5714-{hashlib.sha256(raw_id.encode()).hexdigest()[:12]}"
                self.findings.append(
                    QualityFinding(
                        id=fid,
                        rule_id="python:S5714",
                        rspec_id="RSPEC-5714",
                        category=QualityCategory.RELIABILITY,
                        severity=QualitySeverity.HIGH,
                        title=f"Function '{node.name}' has a mutable default argument.",
                        message=(
                            "Default argument values are evaluated once and shared across calls. "
                            "Use None as default and instantiate within the function."
                        ),
                        file_path=self.file_path,
                        start_line=d.lineno,
                        end_line=getattr(d, "end_lineno", d.lineno),
                        symbol_name=node.name,
                        code_hash=code_hash,
                        non_compliant_snippet=snippet,
                        compliant_snippet=spec_5714.compliant_example if spec_5714 else None,
                        remediation_guidance=spec_5714.explanation if spec_5714 else None,
                    )
                )

    def _check_empty_function(self, node: ast.FunctionDef | ast.AsyncFunctionDef) -> None:
        """S1186: Empty functions without explanation."""
        if (
            len(node.body) == 1
            and isinstance(node.body[0], ast.Pass)
            and not ast.get_docstring(node)
        ):
            snippet = self._get_snippet(node.lineno, getattr(node, "end_lineno", node.lineno))
            code_hash = self._hash_snippet(snippet)
            raw_id = f"{self.file_path}:{node.name}:{node.lineno}"
            fid = f"QUAL-S1186-{hashlib.sha256(raw_id.encode()).hexdigest()[:12]}"
            spec_1186 = QUALITY_RULES_CATALOG.get("S1186")
            self.findings.append(
                QualityFinding(
                    id=fid,
                    rule_id="python:S1186",
                    rspec_id="RSPEC-1186",
                    category=QualityCategory.CODE_SMELL,
                    severity=QualitySeverity.LOW,
                    title=f"Function '{node.name}' is empty without comment or docstring.",
                    message=(
                        f"Add a comment or docstring explaining why '{node.name}' is empty, "
                        "or implement it."
                    ),
                    file_path=self.file_path,
                    start_line=node.lineno,
                    end_line=getattr(node, "end_lineno", node.lineno),
                    symbol_name=node.name,
                    code_hash=code_hash,
                    non_compliant_snippet=snippet,
                    compliant_snippet=spec_1186.compliant_example if spec_1186 else None,
                    remediation_guidance=spec_1186.explanation if spec_1186 else None,
                )
            )

    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
        old_func = self._current_function
        self._current_function = node.name

        self._check_function_param_count(node)
        self._check_mutable_defaults(node)
        self._check_empty_function(node)
        self._check_block_unreachable(node.body)

        self.generic_visit(node)
        self._current_function = old_func

    def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> None:
        self.visit_FunctionDef(node)  # type: ignore[arg-type]

    def _check_mergeable_if(self, node: ast.If) -> None:
        """S1066: Mergeable if statements."""
        if len(node.body) == 1 and isinstance(node.body[0], ast.If):
            inner_if = node.body[0]
            if not inner_if.orelse:
                start_l = node.lineno
                end_l = getattr(inner_if, "end_lineno", inner_if.lineno)
                snippet = self._get_snippet(start_l, end_l)
                code_hash = self._hash_snippet(snippet)
                raw_id = f"{self.file_path}:{start_l}"
                fid = f"QUAL-S1066-{hashlib.sha256(raw_id.encode()).hexdigest()[:12]}"
                spec_1066 = QUALITY_RULES_CATALOG.get("S1066")

                self.findings.append(
                    QualityFinding(
                        id=fid,
                        rule_id="python:S1066",
                        rspec_id="RSPEC-1066",
                        category=QualityCategory.CODE_SMELL,
                        severity=QualitySeverity.MEDIUM,
                        title='Merge this "if" statement with the enclosing one.',
                        message=(
                            "Nested 'if' statements without 'else' should be merged with 'and' "
                            "to decrease nesting."
                        ),
                        file_path=self.file_path,
                        start_line=inner_if.lineno,
                        end_line=getattr(inner_if, "end_lineno", inner_if.lineno),
                        symbol_name=self._current_function,
                        code_hash=code_hash,
                        non_compliant_snippet=snippet,
                        compliant_snippet=spec_1066.compliant_example if spec_1066 else None,
                        remediation_guidance=spec_1066.explanation if spec_1066 else None,
                    )
                )

    def visit_If(self, node: ast.If) -> None:
        self.visit(node.test)
        self._check_mergeable_if(node)
        self._check_block_unreachable(node.body)
        if node.orelse:
            self._check_block_unreachable(node.orelse)

        # S134: Control flow statements should not be nested too deeply
        spec_134 = QUALITY_RULES_CATALOG.get("S134")
        max_depth = spec_134.default_threshold if spec_134 and spec_134.default_threshold else 3

        self._nesting_depth += 1
        if self._nesting_depth > max_depth:
            snippet = self._get_snippet(node.lineno, getattr(node, "end_lineno", node.lineno))
            code_hash = self._hash_snippet(snippet)
            raw_id = f"{self.file_path}:{node.lineno}"
            fid = f"QUAL-S134-{hashlib.sha256(raw_id.encode()).hexdigest()[:12]}"
            self.findings.append(
                QualityFinding(
                    id=fid,
                    rule_id="python:S134",
                    rspec_id="RSPEC-134",
                    category=QualityCategory.MAINTAINABILITY,
                    severity=QualitySeverity.MEDIUM,
                    title=(
                        f"Control flow statement is nested too deeply "
                        f"({self._nesting_depth} > {max_depth})."
                    ),
                    message=(
                        "Reduce nesting level by returning early or extracting inner blocks "
                        "to separate functions."
                    ),
                    file_path=self.file_path,
                    start_line=node.lineno,
                    end_line=getattr(node, "end_lineno", node.lineno),
                    symbol_name=self._current_function,
                    code_hash=code_hash,
                    metric_value=float(self._nesting_depth),
                    metric_threshold=float(max_depth),
                    non_compliant_snippet=snippet[:200],
                    compliant_snippet=spec_134.compliant_example if spec_134 else None,
                    remediation_guidance=spec_134.explanation if spec_134 else None,
                )
            )

        for child in node.body:
            self.visit(child)

        # Handle elif without incrementing nesting level
        orelse = node.orelse
        while orelse and len(orelse) == 1 and isinstance(orelse[0], ast.If):
            elif_node = orelse[0]
            self.visit(elif_node.test)
            self._check_mergeable_if(elif_node)
            self._check_block_unreachable(elif_node.body)
            for child in elif_node.body:
                self.visit(child)
            orelse = elif_node.orelse

        if orelse:
            for child in orelse:
                self.visit(child)

        self._nesting_depth -= 1

    def visit_Compare(self, node: ast.Compare) -> None:
        """S1764 / Google:SelfComparison: Comparing identical expressions."""
        left_dump = ast.dump(node.left)
        for comp in node.comparators:
            if left_dump == ast.dump(comp):
                snippet = self._get_snippet(node.lineno, getattr(node, "end_lineno", node.lineno))
                code_hash = self._hash_snippet(snippet)
                raw_id = f"{self.file_path}:{node.lineno}:self_comparison"
                fid = f"QUAL-S1764-{hashlib.sha256(raw_id.encode()).hexdigest()[:12]}"
                spec_1764 = QUALITY_RULES_CATALOG.get("S1764")
                self.findings.append(
                    QualityFinding(
                        id=fid,
                        rule_id="python:S1764",
                        rspec_id="RSPEC-1764",
                        category=QualityCategory.RELIABILITY,
                        severity=QualitySeverity.HIGH,
                        title=(
                            "Identical expressions should not be on both sides "
                            "of a binary operator."
                        ),
                        message=(
                            "Comparing an expression to itself is redundant or "
                            "indicates a copy-paste error."
                        ),
                        file_path=self.file_path,
                        start_line=node.lineno,
                        end_line=getattr(node, "end_lineno", node.lineno),
                        symbol_name=self._current_function,
                        code_hash=code_hash,
                        non_compliant_snippet=snippet,
                        compliant_snippet=spec_1764.compliant_example if spec_1764 else None,
                        remediation_guidance=spec_1764.explanation if spec_1764 else None,
                    )
                )
        self.generic_visit(node)

    def _extract_dict_key(self, k: ast.expr) -> str | int | None:
        """Extracts constant string or integer key value from AST node."""
        if isinstance(k, ast.Constant) and isinstance(k.value, (str, int)):
            return k.value
        return None

    def _record_duplicate_dict_key(self, k: ast.expr, val: str | int) -> None:
        """Emits S5712 finding for duplicate dictionary keys."""
        lineno = getattr(k, "lineno", 1)
        end_lineno = getattr(k, "end_lineno", lineno)
        snippet = self._get_snippet(lineno, end_lineno)
        code_hash = self._hash_snippet(snippet)
        raw_id = f"{self.file_path}:{lineno}:duplicate_key_{val}"
        fid = f"QUAL-S5712-{hashlib.sha256(raw_id.encode()).hexdigest()[:12]}"
        spec_5712 = QUALITY_RULES_CATALOG.get("S5712")
        self.findings.append(
            QualityFinding(
                id=fid,
                rule_id="python:S5712",
                rspec_id="RSPEC-5712",
                category=QualityCategory.RELIABILITY,
                severity=QualitySeverity.HIGH,
                title=f"Dictionary literal has duplicate key '{val}'.",
                message=(
                    f"Duplicate key '{val}' overwrites earlier entries "
                    "in the same dictionary literal."
                ),
                file_path=self.file_path,
                start_line=lineno,
                end_line=end_lineno,
                symbol_name=self._current_function,
                code_hash=code_hash,
                non_compliant_snippet=snippet,
                compliant_snippet=spec_5712.compliant_example if spec_5712 else None,
                remediation_guidance=spec_5712.explanation if spec_5712 else None,
            )
        )

    def visit_Dict(self, node: ast.Dict) -> None:
        """S5712 / Google:DuplicateKey: Duplicate literal keys in dict literals."""
        seen_keys: set[str | int] = set()
        for k in node.keys:
            if k is None:
                continue
            val = self._extract_dict_key(k)
            if val is None:
                continue
            if val in seen_keys:
                self._record_duplicate_dict_key(k, val)
            else:
                seen_keys.add(val)

        self.generic_visit(node)

    def visit_For(self, node: ast.For) -> None:
        self._check_block_unreachable(node.body)
        self._nesting_depth += 1
        self.generic_visit(node)
        self._nesting_depth -= 1

    def visit_AsyncFor(self, node: ast.AsyncFor) -> None:
        self._check_block_unreachable(node.body)
        self._nesting_depth += 1
        self.generic_visit(node)
        self._nesting_depth -= 1

    def visit_While(self, node: ast.While) -> None:
        self._check_block_unreachable(node.body)
        self._nesting_depth += 1
        self.generic_visit(node)
        self._nesting_depth -= 1

    def visit_Try(self, node: ast.Try) -> None:
        self._check_block_unreachable(node.body)
        self._nesting_depth += 1
        self.generic_visit(node)
        self._nesting_depth -= 1

    def visit_IfExp(self, node: ast.IfExp) -> None:
        """S3358: Nested ternary operators."""
        # Check if any branch of this IfExp contains another IfExp
        has_nested = any(isinstance(sub, ast.IfExp) for sub in (node.body, node.test, node.orelse))
        if has_nested:
            spec_3358 = QUALITY_RULES_CATALOG.get("S3358")
            end_l = getattr(node, "end_lineno", node.lineno)
            snippet = self._get_snippet(node.lineno, end_l)
            code_hash = self._hash_snippet(snippet)
            raw_id = f"{self.file_path}:{node.lineno}:nested_ternary"
            fid = f"QUAL-S3358-{hashlib.sha256(raw_id.encode()).hexdigest()[:12]}"
            self.findings.append(
                QualityFinding(
                    id=fid,
                    rule_id="python:S3358",
                    rspec_id="RSPEC-3358",
                    category=QualityCategory.MAINTAINABILITY,
                    severity=QualitySeverity.MEDIUM,
                    title=(
                        "Extract this nested conditional expression into an independent statement."
                    ),
                    message=(
                        "Nesting ternary conditional expressions degrades readability. "
                        "Refactor into standard if-elif-else blocks."
                    ),
                    file_path=self.file_path,
                    start_line=node.lineno,
                    end_line=end_l,
                    symbol_name=self._current_function,
                    code_hash=code_hash,
                    non_compliant_snippet=snippet[:200],
                    compliant_snippet=spec_3358.compliant_example if spec_3358 else None,
                    remediation_guidance=spec_3358.explanation if spec_3358 else None,
                )
            )

        self.generic_visit(node)


def analyze_python_smells(
    file_path: str,
    source_code: str,
) -> list[QualityFinding]:
    """Analyzes a Python source file for maintainability & reliability anti-patterns."""
    try:
        tree = ast.parse(source_code, filename=file_path)
    except SyntaxError:
        return []

    source_lines = source_code.splitlines()
    visitor = PythonSmellsVisitor(file_path=file_path, source_lines=source_lines)
    visitor.visit(tree)
    return visitor.findings
