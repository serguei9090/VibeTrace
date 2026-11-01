"""Intra- and inter-procedural Def-Use variable taint analyzer for argument provenance."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from vibetrace.security.contracts import FunctionTaintContract, TaintContractManager

_IDENT_RE = re.compile(r"\b([A-Za-z_][A-Za-z0-9_]*)\b")
_ASSIGN_RE = re.compile(
    r"^\s*(?:(?:const|let|var)\s+)?([A-Za-z_][A-Za-z0-9_]*)\s*(?::[^=]+)?=\s*(.+)$"
)
_FUNC_PARAMS_RE = re.compile(r"(?:def|function|func|fn)\s+[A-Za-z0-9_]+\s*\(([^)]*)\)")
_DIRECT_SOURCES = (
    "request.",
    "req.",
    "sys.argv",
    "os.environ",
    "input(",
    "process.argv",
    "params.",
    "query.",
    "body.",
    "c.req.",
    "headers.",
    "searchParams.",
    "Query(",
    "Path(",
    "Body(",
    "Header(",
    "request.POST",
    "request.GET",
    "request.args",
    "c.Query(",
    "c.PostForm(",
    "c.Param(",
)
_KEYWORDS = {
    "def",
    "return",
    "if",
    "else",
    "elif",
    "for",
    "while",
    "in",
    "and",
    "or",
    "not",
    "True",
    "False",
    "None",
    "self",
    "cls",
    "const",
    "let",
    "var",
    "function",
    "async",
    "await",
}


def _extract_params(header_text: str) -> list[str]:
    m = _FUNC_PARAMS_RE.search(header_text)
    if not m:
        return []
    params: list[str] = []
    for raw in m.group(1).split(","):
        part = raw.strip().split(":")[0].split("=")[0].strip().lstrip("*&")
        tokens = _IDENT_RE.findall(part)
        if tokens:
            name = tokens[-1]
            if name not in ("self", "cls", "ctx"):
                params.append(name)
    return params


def _split_top_level_args(arg_str: str) -> list[str]:
    """Splits a call's argument list on top-level commas, respecting quotes and nested parens."""
    args: list[str] = []
    cur: list[str] = []
    depth = 0
    in_quote: str | None = None
    for ch in arg_str:
        if in_quote:
            cur.append(ch)
            if ch == in_quote:
                in_quote = None
            continue
        if ch in ('"', "'", "`"):
            in_quote = ch
            cur.append(ch)
        elif ch in ("(", "[", "{"):
            depth += 1
            cur.append(ch)
        elif ch in (")", "]", "}"):
            if depth > 0:
                depth -= 1
                cur.append(ch)
            else:
                break
        elif ch == "," and depth == 0:
            args.append("".join(cur).strip())
            cur = []
        else:
            cur.append(ch)
    tail = "".join(cur).strip()
    if tail:
        args.append(tail)
    return args


def _eval_helper_call_contract(
    rhs: str,
    contracts_map: dict[str, Any] | None,
    tainted: dict[str, list[str]],
) -> tuple[bool, list[str] | None]:
    """Evaluates whether an RHS helper call sanitizes or propagates taint to the return value."""
    if not contracts_map:
        return False, None
    call_m = re.search(r"\b([A-Za-z_][A-Za-z0-9_]*)\s*\((.*)\)", rhs)
    if not call_m:
        return False, None
    contract = contracts_map.get(call_m.group(1))
    if not contract:
        return False, None
    if contract.is_sanitizer:
        return True, None
    raw_args = _split_top_level_args(call_m.group(2))
    for arg_idx, arg_expr in enumerate(raw_args):
        arg_idents = [tok for tok in _IDENT_RE.findall(arg_expr) if tok not in _KEYWORDS]
        for tok in arg_idents:
            if tok in tainted and contract.param_taints_return.get(arg_idx, True):
                return True, tainted[tok]
    return True, None


def _propagate_function_def_use(
    lines: list[str],
    s_idx: int,
    stop_idx: int,
    sanitizers: frozenset[str],
    tainted_param_indices: dict[int, list[str]] | None = None,
    contracts_map: dict[str, FunctionTaintContract] | None = None,
) -> tuple[list[str], dict[str, list[str]], set[str]]:
    """Propagates Def-Use taint from function header at `s_idx` down to `stop_idx`."""
    header_slice = " ".join(lines[s_idx : min(len(lines), s_idx + 4)])
    params = _extract_params(header_slice)
    tainted: dict[str, list[str]] = {}
    untainted_locals: set[str] = set()

    for idx, p in enumerate(params):
        if tainted_param_indices is None:
            tainted[p] = [p]
        elif idx in tainted_param_indices:
            prefix = tainted_param_indices[idx]
            tainted[p] = [*prefix, p] if p not in prefix else list(prefix)
        else:
            untainted_locals.add(p)

    for idx in range(s_idx + 1, stop_idx):
        line = lines[idx].split("#", 1)[0].strip()
        if not line or "==" in line.split("=", 1)[0]:
            continue
        m = _ASSIGN_RE.match(line)
        if not m:
            continue
        lhs, rhs = m.group(1), m.group(2).strip()
        rhs_idents = [tok for tok in _IDENT_RE.findall(rhs) if tok not in _KEYWORDS]

        if any(f"{san}(" in rhs or f".{san}(" in rhs for san in sanitizers):
            tainted.pop(lhs, None)
            untainted_locals.add(lhs)
            continue

        if any(src in rhs for src in _DIRECT_SOURCES):
            tainted[lhs] = [lhs]
            untainted_locals.discard(lhs)
            continue

        # Check if RHS invokes a helper function with a known Taint Contract
        handled, matched_arg_chain = _eval_helper_call_contract(rhs, contracts_map, tainted)
        if handled:
            if matched_arg_chain:
                tainted[lhs] = (
                    [*matched_arg_chain, lhs] if lhs not in matched_arg_chain else matched_arg_chain
                )
                untainted_locals.discard(lhs)
            else:
                tainted.pop(lhs, None)
                untainted_locals.add(lhs)
            continue

        matched_taint = next((tainted[tok] for tok in rhs_idents if tok in tainted), None)
        if matched_taint is not None:
            tainted[lhs] = [*matched_taint, lhs] if lhs not in matched_taint else matched_taint
            untainted_locals.discard(lhs)
        else:
            tainted.pop(lhs, None)
            untainted_locals.add(lhs)

    return params, tainted, untainted_locals


def verify_sink_taint_flow(
    project_root: Path,
    file_path: str,
    func_start_line: int,
    func_end_line: int,
    sink_line: int,
    sanitizers: frozenset[str],
    tainted_param_indices: dict[int, list[str]] | None = None,
) -> tuple[bool, list[str]]:
    """Checks whether tainted parameters or external sources flow into `sink_line`."""
    abs_path = (project_root / file_path).resolve()
    try:
        content = abs_path.read_text(encoding="utf-8", errors="replace")
        lines = content.splitlines()
        contracts_map = TaintContractManager.analyze_source_code(content, file_path)
    except OSError:
        return True, []

    s_idx = max(0, func_start_line - 1)
    e_idx = min(len(lines), func_end_line)
    k_idx = max(s_idx, min(len(lines) - 1, sink_line - 1))
    if s_idx >= len(lines) or e_idx <= s_idx:
        return True, []

    params, tainted, untainted_locals = _propagate_function_def_use(
        lines, s_idx, k_idx, sanitizers, tainted_param_indices, contracts_map=contracts_map
    )

    sink_text = lines[k_idx].split("#", 1)[0]
    if any(src in sink_text for src in _DIRECT_SOURCES):
        return True, ["untrusted_input"]

    arg_part = sink_text.split("(", 1)[1] if "(" in sink_text else sink_text
    sink_idents = [tok for tok in _IDENT_RE.findall(arg_part) if tok not in _KEYWORDS]

    for tok in sink_idents:
        if tok in tainted:
            return True, tainted[tok]

    if untainted_locals and any(tok in untainted_locals for tok in sink_idents):
        return False, []
    if params and not any(tok in tainted for tok in sink_idents):
        return False, []

    return True, []


def _is_callee_sanitizer(callee: dict[str, Any], project_root: Path, callee_name: str) -> bool:
    """Checks whether the callee function contract marks it as a sanitizer."""
    callee_path = (project_root / str(callee.get("file_path", ""))).resolve()
    if not callee_path.is_file():
        return False
    try:
        callee_content = callee_path.read_text(encoding="utf-8", errors="replace")
        callee_contracts = TaintContractManager.analyze_source_code(
            callee_content, str(callee["file_path"])
        )
        callee_contract = callee_contracts.get(callee_name)
        return bool(callee_contract and callee_contract.is_sanitizer)
    except Exception:
        return False


def _resolve_next_tainted_params(
    args: list[str], sanitizers: frozenset[str], caller_tainted: dict[str, list[str]]
) -> dict[int, list[str]]:
    """Calculates tainted parameters forwarded to callee."""
    next_tainted: dict[int, list[str]] = {}
    for arg_idx, arg_expr in enumerate(args):
        if any(f"{san}(" in arg_expr or f".{san}(" in arg_expr for san in sanitizers):
            continue
        if any(src in arg_expr for src in _DIRECT_SOURCES):
            next_tainted[arg_idx] = ["untrusted_input"]
            continue
        arg_idents = [tok for tok in _IDENT_RE.findall(arg_expr) if tok not in _KEYWORDS]
        for tok in arg_idents:
            if tok in caller_tainted:
                next_tainted[arg_idx] = caller_tainted[tok]
                break
    return next_tainted


def verify_interprocedural_taint_flow(
    project_root: Path,
    path_nodes: list[dict[str, Any]],
    sink_file_path: str,
    sink_line: int,
    sanitizers: frozenset[str],
) -> tuple[bool, list[str], str]:
    """Traces argument-to-parameter taint across a multi-hop call path `source -> ... -> sink`.

    Returns `(is_tainted, variable_chain, reason)` where `reason` is `"tainted"`,
    `"untainted_call_arg"`, or `"untainted_local"`.
    """
    # Filter to concrete function/method hops with line bounds
    hops = [
        n
        for n in path_nodes
        if n.get("kind") in ("function", "method")
        and n.get("file_path")
        and n.get("start_line")
        and n.get("end_line")
    ]
    if not hops:
        return True, [], "tainted"

    tainted_params: dict[int, list[str]] | None = None

    # Walk caller hops (0 .. len-2) to map tainted call arguments -> callee parameter indices
    for i in range(len(hops) - 1):
        caller = hops[i]
        callee = hops[i + 1]
        callee_name = str(callee["name"]).split(".")[-1]
        c_path = (project_root / str(caller["file_path"])).resolve()
        try:
            c_lines = c_path.read_text(encoding="utf-8", errors="replace").splitlines()
        except OSError:
            tainted_params = None
            continue

        s_idx = max(0, int(caller["start_line"]) - 1)
        e_idx = min(len(c_lines), int(caller["end_line"]))
        call_re = re.compile(rf"\b{re.escape(callee_name)}\s*\((.*)$")

        call_line_idx: int | None = None
        call_arg_tail = ""
        for idx in range(s_idx, e_idx):
            code_part = c_lines[idx].split("#", 1)[0]
            m = call_re.search(code_part)
            if m and not code_part.lstrip().startswith(("def ", "function ", "func ", "fn ")):
                call_line_idx = idx
                call_arg_tail = m.group(1)
                break

        if call_line_idx is None:
            tainted_params = None
            continue

        caller_contracts = TaintContractManager.analyze_source_code(
            "\n".join(c_lines), str(caller["file_path"])
        )
        _, caller_tainted, _ = _propagate_function_def_use(
            c_lines,
            s_idx,
            call_line_idx,
            sanitizers,
            tainted_params,
            contracts_map=caller_contracts,
        )
        args = _split_top_level_args(call_arg_tail)
        if not args:
            tainted_params = None
            continue

        if _is_callee_sanitizer(callee, project_root, callee_name):
            return False, [], "sanitized_callee"

        next_tainted_params = _resolve_next_tainted_params(args, sanitizers, caller_tainted)
        if not next_tainted_params:
            return False, [], "untainted_call_arg"
        tainted_params = next_tainted_params

    sink_hop = hops[-1]
    is_tainted, chain = verify_sink_taint_flow(
        project_root=project_root,
        file_path=sink_file_path,
        func_start_line=int(sink_hop["start_line"]),
        func_end_line=int(sink_hop["end_line"]),
        sink_line=sink_line,
        sanitizers=sanitizers,
        tainted_param_indices=tainted_params,
    )
    return is_tainted, chain, ("tainted" if is_tainted else "untainted_local")
