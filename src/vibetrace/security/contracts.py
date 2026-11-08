"""Function Taint Contracts engine for fast, sub-second inter-procedural taint analysis.

Precomputes and caches 1-line function summaries:
- Which parameter indices flow into return expressions (upward flow).
- Which parameter indices reach internal sinks (downward flow).
- Whether the function acts as an input sanitizer/validator.
"""

from __future__ import annotations

import ast
import json
import logging
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from vibetrace.indexer.db import IndexerDB

logger = logging.getLogger(__name__)

# Known sanitizer and validation functions / methods
_KNOWN_SANITIZERS = frozenset(
    {
        "quote",
        "escape",
        "escape_string",
        "resolve",
        "safe_load",
        "literal_eval",
        "is_relative_to",
        "sanitize",
        "validator",
        "validate",
        "clean",
        "encodeURIComponent",
        "DOMPurify",
        "bleach",
        "int",
        "float",
        "bool",
        "shlex.quote",
        "html.escape",
        "urllib.parse.quote",
    }
)

# Common sinks invoked inside function bodies
_SINK_CALL_PATTERNS = {
    "execute": "CWE-89",
    "executemany": "CWE-89",
    "raw": "CWE-89",
    "system": "CWE-78",
    "popen": "CWE-78",
    "subprocess": "CWE-78",
    "run": "CWE-78",
    "eval": "CWE-95",
    "exec": "CWE-95",
    "loads": "CWE-502",
    "open": "CWE-22",
    "read_file": "CWE-22",
}


@dataclass(frozen=True)
class FunctionTaintContract:
    """Precomputed summary of a function's taint input-output behaviors."""

    symbol_id: str
    func_name: str
    file_path: str
    param_names: tuple[str, ...]
    param_taints_return: dict[int, bool]  # param index -> reaches return
    internal_sinks: list[dict[str, Any]]  # sinks called inside body with affected param indices
    is_sanitizer: bool  # returns True if function cleanses or validates input
    dict_aliases: dict[str, str] = field(default_factory=dict)
    key_taints_return: dict[str, list[str]] = field(default_factory=dict)


class _ContractVisitor(ast.NodeVisitor):
    """AST visitor extracting argument-to-return flow and internal sink invocations."""

    def __init__(self, param_names: list[str]) -> None:
        self.param_names = param_names
        self.param_indices = {name: idx for idx, name in enumerate(param_names)}
        self.tainted_locals: dict[str, set[int]] = {p: {idx} for idx, p in enumerate(param_names)}
        self.return_param_indices: set[int] = set()
        self.internal_sinks: list[dict[str, Any]] = []
        self.has_validation_guard: bool = False
        self.has_casting_sanitizer: bool = False
        self.dict_aliases: dict[str, str] = {}
        self.key_taints_return: dict[str, list[str]] = {}
        self.local_key_origins: dict[str, tuple[str, str]] = {}

    def _extract_key_origin(self, expr: ast.AST) -> tuple[str, str] | None:
        if isinstance(expr, ast.Subscript):
            if isinstance(expr.value, ast.Name):
                d_name = expr.value.id
                if d_name in self.param_indices:
                    key = ""
                    if isinstance(expr.slice, ast.Constant) and isinstance(expr.slice.value, str):
                        key = expr.slice.value
                    if key:
                        return d_name, key
        elif isinstance(expr, ast.Call):
            if isinstance(expr.func, ast.Attribute) and expr.func.attr == "get":
                if isinstance(expr.func.value, ast.Name) and expr.args:
                    d_name = expr.func.value.id
                    if d_name in self.param_indices and isinstance(expr.args[0], ast.Constant):
                        if isinstance(expr.args[0].value, str):
                            return d_name, expr.args[0].value
        return None

    def visit_Assign(self, node: ast.Assign) -> None:
        rhs_indices = self._extract_taint_indices_from_expr(node.value)
        key_origin = self._extract_key_origin(node.value)
        for target in node.targets:
            if isinstance(target, ast.Name):
                var_name = target.id
                if rhs_indices:
                    self.tainted_locals[var_name] = set(rhs_indices)
                else:
                    self.tainted_locals.pop(var_name, None)

                if key_origin:
                    self.local_key_origins[var_name] = key_origin
                elif isinstance(node.value, ast.Name) and node.value.id in self.local_key_origins:
                    self.local_key_origins[var_name] = self.local_key_origins[node.value.id]
                    self.dict_aliases[var_name] = node.value.id
        self.generic_visit(node)

    def visit_AnnAssign(self, node: ast.AnnAssign) -> None:
        if node.value:
            rhs_indices = self._extract_taint_indices_from_expr(node.value)
            key_origin = self._extract_key_origin(node.value)
            if isinstance(node.target, ast.Name):
                var_name = node.target.id
                if rhs_indices:
                    self.tainted_locals[var_name] = set(rhs_indices)
                else:
                    self.tainted_locals.pop(var_name, None)

                if key_origin:
                    self.local_key_origins[var_name] = key_origin
                elif isinstance(node.value, ast.Name) and node.value.id in self.local_key_origins:
                    self.local_key_origins[var_name] = self.local_key_origins[node.value.id]
                    self.dict_aliases[var_name] = node.value.id
        self.generic_visit(node)

    def visit_Return(self, node: ast.Return) -> None:
        if node.value:
            indices = self._extract_taint_indices_from_expr(node.value)
            self.return_param_indices.update(indices)
            if isinstance(node.value, ast.Name) and node.value.id in self.local_key_origins:
                p_name, k_name = self.local_key_origins[node.value.id]
                keys = self.key_taints_return.setdefault(p_name, [])
                if k_name not in keys:
                    keys.append(k_name)
        self.generic_visit(node)

    def visit_If(self, node: ast.If) -> None:
        # Check for guard validation: if not re.match(...) or if x in ALLOWED_LIST
        test_str = ast.unparse(node.test) if hasattr(ast, "unparse") else ""
        if any(w in test_str for w in ("match(", "isinstance(", "in ", "isalnum()", "isdigit()")):
            self.has_validation_guard = True
        self.generic_visit(node)

    def visit_Call(self, node: ast.Call) -> None:
        call_name = ""
        if isinstance(node.func, ast.Name):
            call_name = node.func.id
        elif isinstance(node.func, ast.Attribute):
            call_name = node.func.attr

        # Check for sanitizer calls
        if call_name in _KNOWN_SANITIZERS:
            self.has_casting_sanitizer = True

        # Check for internal sinks
        if call_name in _SINK_CALL_PATTERNS:
            cwe = _SINK_CALL_PATTERNS[call_name]
            affected_params: set[int] = set()
            for arg in node.args:
                affected_params.update(self._extract_taint_indices_from_expr(arg))
            if affected_params:
                self.internal_sinks.append(
                    {
                        "call": call_name,
                        "cwe": cwe,
                        "line": getattr(node, "lineno", 1),
                        "param_indices": sorted(affected_params),
                    }
                )

        self.generic_visit(node)

    def _extract_taint_indices_from_expr(self, expr: ast.AST) -> set[int]:
        """Collects all parameter indices that influence an AST expression."""
        found: set[int] = set()
        for node in ast.walk(expr):
            if isinstance(node, ast.Name):
                if node.id in self.tainted_locals:
                    found.update(self.tainted_locals[node.id])
                elif node.id in self.param_indices:
                    found.add(self.param_indices[node.id])
            elif isinstance(node, ast.Call):
                func_name = ""
                if isinstance(node.func, ast.Name):
                    func_name = node.func.id
                elif isinstance(node.func, ast.Attribute):
                    func_name = node.func.attr
                if func_name in _KNOWN_SANITIZERS:
                    self.has_casting_sanitizer = True
                    # Sanitizer neutralizes the taint flowing through this expression
                    return set()
        return found


class TaintContractManager:
    """Computes, caches, and queries Function Taint Contracts in SQLite."""

    @classmethod
    def _init_table(cls, db: IndexerDB) -> None:
        with db._get_conn() as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS function_taint_contracts (
                    symbol_id TEXT PRIMARY KEY,
                    contract_json TEXT NOT NULL,
                    file_hash TEXT NOT NULL,
                    updated_at INTEGER NOT NULL
                )
                """
            )

    @classmethod
    def build_contract_from_ast(
        cls,
        func_def: ast.FunctionDef | ast.AsyncFunctionDef,
        symbol_id: str,
        file_path: str,
    ) -> FunctionTaintContract:
        """Analyzes a FunctionDef AST node and returns its FunctionTaintContract."""
        all_arg_objs = (
            getattr(func_def.args, "posonlyargs", [])
            + func_def.args.args
            + getattr(func_def.args, "kwonlyargs", [])
        )
        param_names: list[str] = [
            arg.arg for arg in all_arg_objs if arg.arg not in ("self", "cls", "ctx", "context")
        ]
        if func_def.args.vararg and func_def.args.vararg.arg not in ("self", "cls", "ctx"):
            param_names.append(func_def.args.vararg.arg)
        if func_def.args.kwarg and func_def.args.kwarg.arg not in ("self", "cls", "ctx"):
            param_names.append(func_def.args.kwarg.arg)

        visitor = _ContractVisitor(param_names)
        visitor.visit(func_def)

        param_taints_return = {
            idx: (idx in visitor.return_param_indices) for idx in range(len(param_names))
        }

        func_lower = func_def.name.lower()
        is_sanitizer_name = any(
            san in func_lower for san in ("sanitize", "escape", "clean", "validate", "filter")
        )
        is_sanitizer = (
            visitor.has_casting_sanitizer
            or (visitor.has_validation_guard and is_sanitizer_name)
            or (is_sanitizer_name and not visitor.return_param_indices)
        )

        return FunctionTaintContract(
            symbol_id=symbol_id,
            func_name=func_def.name,
            file_path=file_path,
            param_names=tuple(param_names),
            param_taints_return=param_taints_return,
            internal_sinks=visitor.internal_sinks,
            is_sanitizer=is_sanitizer,
            dict_aliases=visitor.dict_aliases,
            key_taints_return=visitor.key_taints_return,
        )

    @classmethod
    def analyze_source_code(
        cls, source_code: str, file_path: str
    ) -> dict[str, FunctionTaintContract]:
        """Parses a Python source file and computes contracts for all defined functions."""
        try:
            tree = ast.parse(source_code, filename=file_path)
        except SyntaxError:
            return {}

        contracts: dict[str, FunctionTaintContract] = {}
        norm_path = file_path.replace("\\", "/")

        for node in ast.walk(tree):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                symbol_id = f"{norm_path}::{node.name}"
                contract = cls.build_contract_from_ast(node, symbol_id, norm_path)
                contracts[symbol_id] = contract
                contracts[node.name] = contract  # alias by short name

        return contracts

    @classmethod
    def get_or_build_contract(
        cls,
        db: IndexerDB | None,
        project_root: Path,
        file_path: str,
        func_name: str,
    ) -> FunctionTaintContract | None:
        """Retrieves contract from SQLite cache or computes it on-demand from source file."""
        abs_path = (project_root / file_path).resolve()
        if not abs_path.is_file():
            return None

        try:
            content = abs_path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            return None

        contracts = cls.analyze_source_code(content, file_path)
        return contracts.get(func_name) or contracts.get(f"{file_path}::{func_name}")

    @classmethod
    def persist_contracts(
        cls,
        db: IndexerDB,
        contracts: dict[str, FunctionTaintContract],
        file_hash: str,
        timestamp: int,
    ) -> None:
        """Saves precomputed contracts into the SQLite database."""
        cls._init_table(db)
        with db._get_conn() as conn:
            for sid, contract in contracts.items():
                if "::" in sid:  # only store canonical qualified symbol_ids
                    conn.execute(
                        """
                        INSERT OR REPLACE INTO function_taint_contracts
                        (symbol_id, contract_json, file_hash, updated_at)
                        VALUES (?, ?, ?, ?)
                        """,
                        (
                            contract.symbol_id,
                            json.dumps(asdict(contract)),
                            file_hash,
                            timestamp,
                        ),
                    )
