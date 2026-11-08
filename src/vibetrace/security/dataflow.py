"""Intra- and inter-procedural Def-Use variable taint analyzer for argument provenance."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from vibetrace.security.contracts import FunctionTaintContract, TaintContractManager

_IDENT_RE = re.compile(r"\b([A-Za-z_][A-Za-z0-9_]*)\b")
_ASSIGN_RE = re.compile(
    r"^\s*(?:(?:const|let|var|public|private|protected|static|final|val)\s+)?"
    r"(?:[A-Za-z0-9_<>,\[\]?]+\s+)?"
    r"([A-Za-z_$][A-Za-z0-9_$]*)\s*(?::[^=]+)?(\+?=|\|=|:=)\s*(.+)$"
)
_DICT_ACCESS_RE = re.compile(
    r'([A-Za-z_$][A-Za-z0-9_$]*)\s*(?:\[\s*["\']([^"\']+)["\']\s*\]|\.get\s*\(\s*["\']([^"\']+)["\']\s*(?:,\s*[^)]+)?\))'
)
_SAFE_DICT_KEYS = frozenset(
    {"page", "per_page", "limit", "offset", "count", "format", "version", "verbose"}
)
_FUNC_PARAMS_RE = re.compile(
    r"(?:(?:def|function|func|fn)\s+(?:\([^)]*\)\s*)?[A-Za-z0-9_$]+|"
    r"(?:(?:public|private|protected|static|async|override)\s+)*[A-Za-z0-9_<>,\[\]?]+\s+[A-Za-z0-9_$]+|"
    r"(?:const|let|var)\s+[A-Za-z0-9_$]+\s*=\s*(?:async\s*)?)\s*\(([^)]*)\)"
)
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
        # Fallback for arrow functions like: (req, res) => or (req: Request, res: Response) =>
        m = re.search(r"\(([^)]*)\)\s*(?::\s*[^=>]+)?\s*=>", header_text)
        if not m:
            return []
    params: list[str] = []
    for raw in m.group(1).split(","):
        trimmed = raw.strip()
        if not trimmed:
            continue
        words = trimmed.split()
        # TypeScript: `name: Type` or Python `name: type = val`
        if ":" in trimmed:
            part = trimmed.split(":")[0].strip().lstrip("*&$")
            tokens = _IDENT_RE.findall(part)
            if tokens and tokens[-1].lower() not in ("self", "cls", "ctx", "context", "this"):
                params.append(tokens[-1])
            continue

        # Go: `varName Type` vs Java/C/C++/C#: `Type varName`
        if len(words) >= 2 and not trimmed.startswith(("final ", "const ", "let ", "var ")):
            type_first_keywords = {
                "string",
                "int",
                "long",
                "short",
                "byte",
                "char",
                "boolean",
                "bool",
                "float",
                "double",
                "void",
                "object",
                "list",
                "map",
                "set",
            }
            first_w = words[0].rstrip("*&[]?").lower()
            if first_w in type_first_keywords or words[0].startswith(
                ("ResponseEntity", "Map<", "List<", "Set<")
            ):
                ident = words[-1].split("=")[0].strip().lstrip("*&$")
                if _IDENT_RE.fullmatch(ident) and ident.lower() not in (
                    "self",
                    "cls",
                    "ctx",
                    "context",
                    "this",
                ):
                    params.append(ident)
                    continue

            # Default Go `varName Type`: first token is the identifier
            first_ident = words[0].lstrip("*&")
            if _IDENT_RE.fullmatch(first_ident):
                if first_ident.lower() not in ("self", "cls", "ctx", "context", "this"):
                    params.append(first_ident)
                continue

        # Python / vanilla JS untyped: `def func(a, b)`
        part = trimmed.split("=")[0].strip().lstrip("*&$")
        tokens = _IDENT_RE.findall(part)
        if tokens:
            name = tokens[-1]
            if name.lower() not in ("self", "cls", "ctx", "context", "this"):
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
            # Check in-place mutation like query.append(user_val) or list.extend(items)
            mut_m = re.match(
                r"^([A-Za-z_][A-Za-z0-9_]*)\.(?:append|extend|update)\s*\((.+)\)$",
                line,
            )
            if mut_m:
                mut_target, mut_arg = mut_m.group(1), mut_m.group(2)
                mut_idents = [tok for tok in _IDENT_RE.findall(mut_arg) if tok not in _KEYWORDS]
                matched_mut = next((tainted[t] for t in mut_idents if t in tainted), None)
                if matched_mut or any(src in mut_arg for src in _DIRECT_SOURCES):
                    origin = matched_mut or ["untrusted_input"]
                    tainted[mut_target] = [*origin, mut_target]
                    untainted_locals.discard(mut_target)
            continue
        lhs, op, rhs = m.group(1), m.group(2), m.group(3).strip()
        rhs_idents = [tok for tok in _IDENT_RE.findall(rhs) if tok not in _KEYWORDS]

        if any(f"{san}(" in rhs or f".{san}(" in rhs for san in sanitizers):
            tainted.pop(lhs, None)
            untainted_locals.add(lhs)
            continue

        # Check dictionary field access: e.g. user_id = params["id"] or params.get("page")
        dict_m = _DICT_ACCESS_RE.search(rhs)
        if dict_m:
            dict_var = dict_m.group(1)
            key_name = dict_m.group(2) or dict_m.group(3) or ""
            is_tainted_dict = dict_var in tainted or any(
                src.startswith(f"{dict_var}.") for src in _DIRECT_SOURCES
            )
            if is_tainted_dict:
                if key_name.lower() in _SAFE_DICT_KEYS or any(
                    f"{san}(" in rhs for san in sanitizers
                ):
                    tainted.pop(lhs, None)
                    untainted_locals.add(lhs)
                else:
                    origin = tainted.get(dict_var, [dict_var])
                    key_tag = f"{dict_var}[{key_name}]"
                    tainted[lhs] = [*origin, key_tag, lhs]
                    untainted_locals.discard(lhs)
                continue

        if any(src in rhs for src in _DIRECT_SOURCES):
            tainted[lhs] = [lhs]
            untainted_locals.discard(lhs)
            continue

        # Check direct variable alias: alias = user_id
        if rhs in tainted:
            tainted[lhs] = [*tainted[rhs], lhs]
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
        elif op == "=":
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
    args: list[str],
    sanitizers: frozenset[str],
    caller_tainted: dict[str, list[str]],
    callee_param_names: list[str] | None = None,
) -> dict[int, list[str]]:
    """Calculates tainted parameters forwarded to callee, supporting positional and keyword args."""
    next_tainted: dict[int, list[str]] = {}
    for arg_idx, arg_expr in enumerate(args):
        kw_m = re.match(r"^([A-Za-z_][A-Za-z0-9_]*)\s*=\s*(.+)$", arg_expr)
        if kw_m and callee_param_names and kw_m.group(1) in callee_param_names:
            target_idx = callee_param_names.index(kw_m.group(1))
            val_expr = kw_m.group(2)
        else:
            target_idx = arg_idx
            val_expr = kw_m.group(2) if kw_m else arg_expr

        if any(f"{san}(" in val_expr or f".{san}(" in val_expr for san in sanitizers):
            continue
        if any(src in val_expr for src in _DIRECT_SOURCES):
            next_tainted[target_idx] = ["untrusted_input"]
            continue
        arg_idents = [tok for tok in _IDENT_RE.findall(val_expr) if tok not in _KEYWORDS]
        for tok in arg_idents:
            if tok in caller_tainted:
                next_tainted[target_idx] = caller_tainted[tok]
                break
    return next_tainted


def _extract_call_args_multiline(lines: list[str], start_idx: int, initial_tail: str) -> list[str]:
    """Extracts call argument strings across single- or multi-line function invocations."""
    combined = initial_tail
    depth = 1
    in_quote: str | None = None

    for ch in initial_tail:
        if in_quote:
            if ch == in_quote:
                in_quote = None
            continue
        if ch in ('"', "'", "`"):
            in_quote = ch
        elif ch in ("(", "[", "{"):
            depth += 1
        elif ch in (")", "]", "}"):
            depth -= 1
            if depth == 0:
                break

    curr = start_idx + 1
    while depth > 0 and curr < len(lines):
        line = lines[curr].split("#", 1)[0]
        for ch in line:
            if in_quote:
                if ch == in_quote:
                    in_quote = None
                continue
            if ch in ('"', "'", "`"):
                in_quote = ch
            elif ch in ("(", "[", "{"):
                depth += 1
            elif ch in (")", "]", "}"):
                depth -= 1
                if depth == 0:
                    break
        combined += " " + line
        curr += 1

    return _split_top_level_args(combined)


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

        # Find all call sites of callee_name within caller body
        call_sites: list[tuple[int, str]] = []
        for idx in range(s_idx, e_idx):
            code_part = c_lines[idx].split("#", 1)[0]
            m = call_re.search(code_part)
            if m and not code_part.lstrip().startswith(("def ", "function ", "func ", "fn ")):
                call_sites.append((idx, m.group(1)))

        if not call_sites:
            tainted_params = None
            continue

        caller_contracts = TaintContractManager.analyze_source_code(
            "\n".join(c_lines), str(caller["file_path"])
        )

        if _is_callee_sanitizer(callee, project_root, callee_name):
            return False, [], "sanitized_callee"

        callee_path = (project_root / str(callee.get("file_path", ""))).resolve()
        callee_params: list[str] | None = None
        if callee_path.is_file():
            try:
                cal_lines = callee_path.read_text(encoding="utf-8", errors="replace").splitlines()
                c_s_idx = max(0, int(callee["start_line"]) - 1)
                callee_header = " ".join(cal_lines[c_s_idx : min(len(cal_lines), c_s_idx + 4)])
                callee_params = _extract_params(callee_header)
            except Exception:
                callee_params = None

        aggregated_tainted_params: dict[int, list[str]] = {}
        for call_line_idx, call_arg_tail in call_sites:
            _, caller_tainted, _ = _propagate_function_def_use(
                c_lines,
                s_idx,
                call_line_idx,
                sanitizers,
                tainted_params,
                contracts_map=caller_contracts,
            )
            args = _extract_call_args_multiline(c_lines, call_line_idx, call_arg_tail)
            if not args:
                continue

            site_tainted_params = _resolve_next_tainted_params(
                args, sanitizers, caller_tainted, callee_params
            )
            for p_idx, chain in site_tainted_params.items():
                if p_idx not in aggregated_tainted_params:
                    aggregated_tainted_params[p_idx] = chain

        if not aggregated_tainted_params:
            return False, [], "untainted_call_arg"
        tainted_params = aggregated_tainted_params

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
