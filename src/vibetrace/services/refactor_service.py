"""Interactive AST Auto-Refactor Service.

Analyzes AST variable lifetimes (def-use chains) to safely extract complex
statement blocks into decoupled helper methods without variable scoping regressions.
"""

from __future__ import annotations

import ast
import builtins
import difflib
import textwrap
from pathlib import Path

from vibetrace.quality.complexity import PythonCognitiveVisitor
from vibetrace.refactor.models import CandidateSplitBlock, RefactorPlan

_PYTHON_BUILTINS = frozenset(dir(builtins)) | {
    "None",
    "True",
    "False",
    "Path",
    "Any",
    "Optional",
    "Union",
    "List",
    "Dict",
    "Set",
    "Tuple",
}


class _BlockUsageVisitor(ast.NodeVisitor):
    """Gathers variable reads, writes, returns, and control-flow jumps in an AST subtree."""

    def __init__(self) -> None:
        self.read_vars: list[str] = []
        self.written_vars: list[str] = []
        self.first_access: dict[str, str] = {}
        self.has_yield: bool = False
        self.has_nonlocal: bool = False
        self.has_global: bool = False
        self.unscoped_loop_jump: bool = False
        self.returns: list[ast.Return] = []
        self._loop_depth: int = 0

    def visit_Name(self, node: ast.Name) -> None:
        if node.id not in self.first_access:
            if isinstance(node.ctx, ast.Load):
                self.first_access[node.id] = "load"
            elif isinstance(node.ctx, (ast.Store, ast.Del)):
                self.first_access[node.id] = "store"

        if isinstance(node.ctx, ast.Load):
            if node.id not in self.read_vars:
                self.read_vars.append(node.id)
        elif isinstance(node.ctx, (ast.Store, ast.Del)):
            if node.id not in self.written_vars:
                self.written_vars.append(node.id)
        self.generic_visit(node)

    def visit_AugAssign(self, node: ast.AugAssign) -> None:
        if isinstance(node.target, ast.Name):
            if node.target.id not in self.first_access:
                self.first_access[node.target.id] = "load"
            if node.target.id not in self.read_vars:
                self.read_vars.append(node.target.id)
            if node.target.id not in self.written_vars:
                self.written_vars.append(node.target.id)
        self.visit(node.value)

    def visit_For(self, node: ast.For) -> None:
        self._loop_depth += 1
        self.generic_visit(node)
        self._loop_depth -= 1

    def visit_AsyncFor(self, node: ast.AsyncFor) -> None:
        self._loop_depth += 1
        self.generic_visit(node)
        self._loop_depth -= 1

    def visit_While(self, node: ast.While) -> None:
        self._loop_depth += 1
        self.generic_visit(node)
        self._loop_depth -= 1

    def visit_Break(self, node: ast.Break) -> None:
        if self._loop_depth <= 0:
            self.unscoped_loop_jump = True

    def visit_Continue(self, node: ast.Continue) -> None:
        if self._loop_depth <= 0:
            self.unscoped_loop_jump = True

    def visit_Yield(self, node: ast.Yield) -> None:
        self.has_yield = True
        self.generic_visit(node)

    def visit_YieldFrom(self, node: ast.YieldFrom) -> None:
        self.has_yield = True
        self.generic_visit(node)

    def visit_Nonlocal(self, node: ast.Nonlocal) -> None:
        self.has_nonlocal = True

    def visit_Global(self, node: ast.Global) -> None:
        self.has_global = True

    def visit_Return(self, node: ast.Return) -> None:
        self.returns.append(node)
        self.generic_visit(node)


def _extract_func_parameters(func_def: ast.FunctionDef | ast.AsyncFunctionDef) -> list[str]:
    """Extracts positional and keyword argument names for a function."""
    params: list[str] = []
    all_args = func_def.args.posonlyargs + func_def.args.args + func_def.args.kwonlyargs
    for arg in all_args:
        if arg.arg not in ("self", "cls"):
            params.append(arg.arg)
    if func_def.args.vararg and func_def.args.vararg.arg not in ("self", "cls"):
        params.append(func_def.args.vararg.arg)
    if func_def.args.kwarg and func_def.args.kwarg.arg not in ("self", "cls"):
        params.append(func_def.args.kwarg.arg)
    return params


def _compute_block_complexity(stmts: list[ast.stmt]) -> int:
    """Calculates cognitive complexity score for a sequence of AST statements."""
    visitor = PythonCognitiveVisitor()
    for stmt in stmts:
        visitor.visit(stmt)
    return visitor.cognitive_score


def _find_target_function(
    tree: ast.AST, func_name: str
) -> tuple[ast.FunctionDef | ast.AsyncFunctionDef, int] | None:
    """Locates the target function definition and its start line."""
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == func_name:
            return node, node.lineno
    return None


class RefactorService:
    """Main orchestrator for AST split method analysis, suggestion, and code rewriting."""

    @classmethod
    def suggest_split_candidates(
        cls, project_root: Path, file_path: str, func_name: str
    ) -> list[CandidateSplitBlock]:
        """Analyzes a function and returns ranked statement blocks suitable for extraction."""
        abs_path = (project_root / file_path).resolve()
        if not abs_path.is_file():
            raise FileNotFoundError(f"Source file not found: {abs_path}")

        source = abs_path.read_text(encoding="utf-8")
        tree = ast.parse(source, filename=str(abs_path))
        target_info = _find_target_function(tree, func_name)
        if not target_info:
            raise ValueError(f"Function '{func_name}' not found in {file_path}")

        func_def, _ = target_info
        func_params = _extract_func_parameters(func_def)
        body = func_def.body

        candidates: list[CandidateSplitBlock] = []

        # 1. Evaluate single compound statement candidates (If, For, While, Try, With)
        for idx, stmt in enumerate(body):
            start_line = stmt.lineno
            end_line = getattr(stmt, "end_lineno", start_line)
            stmt_type = type(stmt).__name__

            block_complexity = _compute_block_complexity([stmt])
            if block_complexity < 1 and (end_line - start_line < 4):
                continue

            inputs, outputs, rejection = cls._analyze_block_lifetimes(
                body=body,
                start_idx=idx,
                end_idx=idx,
                func_params=func_params,
            )

            if rejection is None:
                candidates.append(
                    CandidateSplitBlock(
                        start_line=start_line,
                        end_line=end_line,
                        complexity_score=block_complexity,
                        complexity_reduction=block_complexity,
                        inputs=inputs,
                        outputs=outputs,
                        statement_types=[stmt_type],
                        summary=(
                            f"Lines {start_line}-{end_line} ({stmt_type}): "
                            f"reduces cognitive complexity by {block_complexity}; "
                            f"inputs={inputs}; outputs={outputs}"
                        ),
                    )
                )

        # 2. Evaluate contiguous multi-statement slices (e.g. 2 to 5 consecutive statements)
        if len(body) >= 2:
            for i in range(len(body)):
                for j in range(i + 1, min(i + 4, len(body))):
                    if i == 0 and j >= len(body) - 1:
                        continue
                    slice_stmts = body[i : j + 1]
                    s_line = slice_stmts[0].lineno
                    e_line = getattr(slice_stmts[-1], "end_lineno", s_line)
                    block_complexity = _compute_block_complexity(slice_stmts)

                    if block_complexity >= 2 or (e_line - s_line >= 6):
                        inputs, outputs, rejection = cls._analyze_block_lifetimes(
                            body=body,
                            start_idx=i,
                            end_idx=j,
                            func_params=func_params,
                        )
                        if rejection is None:
                            types = [type(s).__name__ for s in slice_stmts]
                            candidates.append(
                                CandidateSplitBlock(
                                    start_line=s_line,
                                    end_line=e_line,
                                    complexity_score=block_complexity,
                                    complexity_reduction=block_complexity,
                                    inputs=inputs,
                                    outputs=outputs,
                                    statement_types=types,
                                    summary=(
                                        f"Lines {s_line}-{e_line} ({', '.join(types)}): "
                                        f"reduces cognitive complexity by {block_complexity}; "
                                        f"inputs={inputs}; outputs={outputs}"
                                    ),
                                )
                            )

        # Sort: highest complexity reduction first, fewest statement types, least variable coupling
        candidates.sort(
            key=lambda c: (
                -c.complexity_reduction,
                len(c.statement_types),
                len(c.inputs) + len(c.outputs),
                c.start_line,
            )
        )
        return candidates

    @classmethod
    def _analyze_block_lifetimes(
        cls,
        body: list[ast.stmt],
        start_idx: int,
        end_idx: int,
        func_params: list[str],
    ) -> tuple[list[str], list[str], str | None]:
        """Calculates inputs, outputs, and checks pre-flight SESE constraints."""
        prior_stmts = body[:start_idx]
        block_stmts = body[start_idx : end_idx + 1]
        after_stmts = body[end_idx + 1 :]

        # Prior definitions
        prior_visitor = _BlockUsageVisitor()
        for s in prior_stmts:
            prior_visitor.visit(s)
        available_prior = set(func_params) | set(prior_visitor.written_vars)

        # Block inspection
        block_visitor = _BlockUsageVisitor()
        for s in block_stmts:
            block_visitor.visit(s)

        # Pre-flight rejection checks
        if block_visitor.has_yield:
            return [], [], "Block contains yield or yield from expressions"
        if block_visitor.has_nonlocal:
            return [], [], "Block contains nonlocal declarations"
        if block_visitor.unscoped_loop_jump:
            return [], [], "Block contains break/continue targeting an outer loop"
        if block_visitor.returns:
            return [], [], "Block contains return statements"

        # Subsequent reads
        after_visitor = _BlockUsageVisitor()
        for s in after_stmts:
            after_visitor.visit(s)

        # Inputs = Read in block that were defined prior
        inputs = [
            var
            for var in block_visitor.read_vars
            if (var in available_prior or var in func_params)
            and var not in _PYTHON_BUILTINS
            and block_visitor.first_access.get(var) == "load"
        ]

        # Outputs = Assigned in block that are subsequently read
        outputs = [
            var
            for var in block_visitor.written_vars
            if var in after_visitor.read_vars and var not in _PYTHON_BUILTINS
        ]

        return inputs, outputs, None

    @classmethod
    def build_refactor_plan(
        cls,
        project_root: Path,
        file_path: str,
        func_name: str,
        start_line: int | None = None,
        end_line: int | None = None,
        helper_name: str | None = None,
    ) -> RefactorPlan:
        """Constructs an executable refactoring plan with extracted code and caller replacement."""
        abs_path = (project_root / file_path).resolve()
        if not abs_path.is_file():
            raise FileNotFoundError(f"Source file not found: {abs_path}")

        source = abs_path.read_text(encoding="utf-8")
        tree = ast.parse(source, filename=str(abs_path))
        target_info = _find_target_function(tree, func_name)
        if not target_info:
            raise ValueError(f"Function '{func_name}' not found in {file_path}")

        func_def, _ = target_info
        func_params = _extract_func_parameters(func_def)
        body = func_def.body

        # Determine target statement slice
        start_idx = 0
        end_idx = 0
        if start_line is not None and end_line is not None:
            matching_indices = [
                idx
                for idx, s in enumerate(body)
                if not (getattr(s, "end_lineno", s.lineno) < start_line or s.lineno > end_line)
            ]
            if not matching_indices:
                raise ValueError(
                    f"No statements found in '{func_name}' within lines {start_line}-{end_line}"
                )
            start_idx = matching_indices[0]
            end_idx = matching_indices[-1]
        else:
            candidates = cls.suggest_split_candidates(project_root, file_path, func_name)
            if not candidates:
                raise ValueError(f"No safe candidate split blocks detected in '{func_name}'")
            top = candidates[0]
            start_line = top.start_line
            end_line = top.end_line
            matching_indices = [
                idx
                for idx, s in enumerate(body)
                if not (getattr(s, "end_lineno", s.lineno) < start_line or s.lineno > end_line)
            ]
            start_idx = matching_indices[0]
            end_idx = matching_indices[-1]

        target_stmts = body[start_idx : end_idx + 1]
        actual_start_line = target_stmts[0].lineno
        actual_end_line = getattr(target_stmts[-1], "end_lineno", actual_start_line)

        inputs, outputs, rejection = cls._analyze_block_lifetimes(
            body=body,
            start_idx=start_idx,
            end_idx=end_idx,
            func_params=func_params,
        )

        lines = source.splitlines(keepends=True)
        original_snippet = "".join(lines[actual_start_line - 1 : actual_end_line])

        if not helper_name:
            helper_name = f"_{func_name}_helper"

        if rejection is not None:
            return RefactorPlan(
                target_function=func_name,
                file_path=file_path,
                start_line=actual_start_line,
                end_line=actual_end_line,
                helper_name=helper_name,
                helper_code="",
                call_replacement="",
                original_snippet=original_snippet,
                refactored_full_source=source,
                can_auto_apply=False,
                rejection_reason=rejection,
                diff="",
                inputs=inputs,
                outputs=outputs,
            )

        # Detect block indentation
        first_line_raw = lines[actual_start_line - 1]
        block_indent = len(first_line_raw) - len(first_line_raw.lstrip())

        # Determine function definition indentation
        func_line_raw = lines[func_def.lineno - 1]
        func_indent = len(func_line_raw) - len(func_line_raw.lstrip())
        indent_str = " " * func_indent

        # Format helper function code
        dedented_block = textwrap.dedent(original_snippet)
        indented_body = textwrap.indent(dedented_block, "    ")

        return_stmt = ""
        if outputs:
            return_stmt = f"    return {', '.join(outputs)}\n"

        args_str = ", ".join(inputs)
        helper_code = f"{indent_str}def {helper_name}({args_str}):\n{indented_body}"
        if not helper_code.endswith("\n"):
            helper_code += "\n"
        if return_stmt:
            helper_code += textwrap.indent(return_stmt, indent_str)

        # Format call replacement statement
        call_indent_str = " " * block_indent
        if len(outputs) == 1:
            call_replacement = f"{call_indent_str}{outputs[0]} = {helper_name}({args_str})\n"
        elif len(outputs) > 1:
            call_replacement = (
                f"{call_indent_str}{', '.join(outputs)} = {helper_name}({args_str})\n"
            )
        else:
            call_replacement = f"{call_indent_str}{helper_name}({args_str})\n"

        # Reconstruct full refactored source
        new_lines: list[str] = []
        # 1. Lines before function definition
        new_lines.extend(lines[: func_def.lineno - 1])
        # 2. Inject helper definition above function
        new_lines.append(helper_code)
        new_lines.append("\n")
        # 3. Lines from function definition up to start of extracted block
        new_lines.extend(lines[func_def.lineno - 1 : actual_start_line - 1])
        # 4. Replacement call site
        new_lines.append(call_replacement)
        # 5. Lines after extracted block
        new_lines.extend(lines[actual_end_line:])

        refactored_full_source = "".join(new_lines)

        # Generate unified diff
        diff_lines = difflib.unified_diff(
            lines,
            new_lines,
            fromfile=f"a/{file_path}",
            tofile=f"b/{file_path}",
        )
        diff = "".join(diff_lines)

        return RefactorPlan(
            target_function=func_name,
            file_path=file_path,
            start_line=actual_start_line,
            end_line=actual_end_line,
            helper_name=helper_name,
            helper_code=helper_code,
            call_replacement=call_replacement,
            original_snippet=original_snippet,
            refactored_full_source=refactored_full_source,
            can_auto_apply=True,
            rejection_reason=None,
            diff=diff,
            inputs=inputs,
            outputs=outputs,
        )

    @classmethod
    def apply_refactor_plan(cls, project_root: Path, plan: RefactorPlan) -> None:
        """Applies a verified RefactorPlan to disk."""
        if not plan.can_auto_apply:
            raise ValueError(f"Cannot apply refactor plan: {plan.rejection_reason}")

        abs_path = (project_root / plan.file_path).resolve()
        abs_path.write_text(plan.refactored_full_source, encoding="utf-8")
