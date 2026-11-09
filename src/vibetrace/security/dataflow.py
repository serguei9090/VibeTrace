"""Intra- and inter-procedural Def-Use variable taint analyzer for argument provenance."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from vibetrace.security.contracts import FunctionTaintContract, TaintContractManager

_ENCODING_ERRORS = "replace"
_KEY_FILE_PATH = "file_path"
_KEY_START_LINE = "start_line"
_KEY_END_LINE = "end_line"
_UNTRUSTED_INPUT = "untrusted_input"
_IGNORED_PARAM_NAMES = frozenset({"self", "cls", "ctx", "context", "this"})

_IDENT_RE = re.compile(r"\b([A-Za-z_]\w*)\b")
_ASSIGN_OP_RE = re.compile(r"([A-Za-z_$][\w$]*)\s*(?::[^=]+)?([+|:]?=)")
_DICT_BRACKET_RE = re.compile(r'([A-Za-z_$][\w$]*)\s*\[\s*["\']([^"\']+)["\']\s*\]')
_DICT_GET_RE = re.compile(r'([A-Za-z_$][\w$]*)\.get\s*\(\s*["\']([^"\']+)["\']')
_MUTATION_CALL_RE = re.compile(r"^([A-Za-z_]\w*)\.(?:append|extend|update)\s*\(")
_SAFE_DICT_KEYS = frozenset(
    {"page", "per_page", "limit", "offset", "count", "format", "version", "verbose"}
)
_FUNC_DEF_RE = re.compile(r"\b(?:def|function)\s+[A-Za-z_$][\w$]*\s*\(([^)]*)\)")
_FUNC_GO_RUST_RE = re.compile(r"\b(?:func|fn)\s+(?:\([^)]*\)\s*)?[A-Za-z_$][\w$]*\s*\(([^)]*)\)")
_METHOD_DECL_RE = re.compile(r"(?:[\w<>,\[\]?]+\s+){1,4}[A-Za-z_$][\w$]*\s*\(([^)]*)\)")
_ARROW_ASSIGN_RE = re.compile(
    r"(?:const|let|var)\s+[A-Za-z_$][\w$]*\s*=\s*(?:async\s*)?\(([^)]*)\)"
)
_TYPE_FIRST_KEYWORDS = frozenset(
    {
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


def _match_param_group(header_text: str) -> str | None:
    """Extracts the raw parenthesis parameter block from a function or arrow header."""
    for rx in (_FUNC_DEF_RE, _FUNC_GO_RUST_RE, _ARROW_ASSIGN_RE, _METHOD_DECL_RE):
        m = rx.search(header_text)
        if m:
            return m.group(1)
    if "=>" in header_text and "(" in header_text:
        arrow_prefix = header_text.split("=>", 1)[0]
        open_idx = arrow_prefix.find("(")
        close_idx = arrow_prefix.find(")", open_idx + 1)
        if 0 <= open_idx < close_idx:
            return arrow_prefix[open_idx + 1 : close_idx]
    return None


def _parse_single_param(trimmed: str) -> str | None:
    """Extracts the parameter identifier from a single declaration item."""
    if not trimmed:
        return None

    # Strip annotations (e.g. @Valid, @NotNull) and modifiers (final, const, let, var, etc.)
    # Repeatedly remove leading annotations @Word[(...)] or modifier words
    curr = trimmed.strip()
    while True:
        if curr.startswith("@"):
            # Strip annotation e.g. @RequestParam(...) or @Valid
            m = re.match(r"^@\w+(?:\([^)]*\))?\s*", curr)
            if m:
                curr = curr[m.end() :].strip()
                continue
        m_mod = re.match(
            r"^(?:final|const|let|var|public|protected|private|static|readonly)\b\s*", curr
        )
        if m_mod:
            curr = curr[m_mod.end() :].strip()
            continue
        break

    if not curr:
        return None

    if ":" in curr:
        part = curr.split(":", 1)[0].strip().lstrip("*&$")
        tokens = _IDENT_RE.findall(part)
        return tokens[-1] if tokens else None

    words = curr.split()
    if len(words) >= 2:
        first_w = words[0].rstrip("*&[]?").lower()
        is_java_type = first_w in _TYPE_FIRST_KEYWORDS or words[0].startswith(
            ("ResponseEntity", "Map<", "List<", "Set<")
        )
        if is_java_type:
            part = "".join(words[1:]).split("=", 1)[0].strip().lstrip("*&$")
            tokens = _IDENT_RE.findall(part)
            return tokens[-1] if tokens else None
        first_ident = words[0].lstrip("*&")
        if _IDENT_RE.fullmatch(first_ident):
            return first_ident

    part = curr.split("=", 1)[0].strip().lstrip("*&$")
    tokens = _IDENT_RE.findall(part)
    return tokens[-1] if tokens else None


def _extract_params(header_text: str) -> list[str]:
    raw_group = _match_param_group(header_text)
    if raw_group is None:
        return []
    params: list[str] = []
    for raw in raw_group.split(","):
        candidate = _parse_single_param(raw.strip())
        if candidate and candidate.lower() not in _IGNORED_PARAM_NAMES:
            params.append(candidate)
    return params


def _advance_delimiter_state(
    ch: str, depth: int, in_quote: str | None
) -> tuple[int, str | None, bool]:
    """Updates quote and bracket depth state for a single character."""
    if in_quote:
        return depth, (None if ch == in_quote else in_quote), False
    if ch in ('"', "'", "`"):
        return depth, ch, False
    if ch in ("(", "[", "{"):
        return depth + 1, None, False
    if ch in (")", "]", "}"):
        if depth > 0:
            return depth - 1, None, False
        return depth, None, True
    return depth, None, False


def _split_top_level_args(arg_str: str) -> list[str]:
    """Splits a call's argument list on top-level commas, respecting quotes and nested parens."""
    args: list[str] = []
    cur: list[str] = []
    depth = 0
    in_quote: str | None = None
    for ch in arg_str:
        if not in_quote and depth == 0 and ch == ",":
            args.append("".join(cur).strip())
            cur = []
            continue
        depth, in_quote, closed = _advance_delimiter_state(ch, depth, in_quote)
        if closed:
            break
        cur.append(ch)
    tail = "".join(cur).strip()
    if tail:
        args.append(tail)
    return args


def _parse_call_name_and_args(expr: str) -> tuple[str, str] | None:
    """Extracts `(func_name, inner_args_str)` from a call expression without backtracking."""
    open_paren = expr.find("(")
    close_paren = expr.rfind(")")
    if open_paren <= 0 or close_paren <= open_paren:
        return None
    prefix = expr[:open_paren].strip()
    tokens = _IDENT_RE.findall(prefix)
    if not tokens:
        return None
    return tokens[-1], expr[open_paren + 1 : close_paren]


def _eval_helper_call_contract(
    rhs: str,
    contracts_map: dict[str, Any] | None,
    tainted: dict[str, list[str]],
) -> tuple[bool, list[str] | None]:
    """Evaluates whether an RHS helper call sanitizes or propagates taint to the return value."""
    if not contracts_map:
        return False, None
    parsed = _parse_call_name_and_args(rhs)
    if not parsed:
        return False, None
    func_name, raw_arg_str = parsed
    contract = contracts_map.get(func_name)
    if not contract:
        return False, None
    if contract.is_sanitizer:
        return True, None
    for arg_idx, arg_expr in enumerate(_split_top_level_args(raw_arg_str)):
        arg_idents = [tok for tok in _IDENT_RE.findall(arg_expr) if tok not in _KEYWORDS]
        for tok in arg_idents:
            if tok in tainted and contract.param_taints_return.get(arg_idx, True):
                return True, tainted[tok]
    return True, None


def _init_param_taint(
    params: list[str],
    tainted_param_indices: dict[int, list[str]] | None,
) -> tuple[dict[str, list[str]], set[str]]:
    """Initializes parameter taint state and untainted local set for a function."""
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
    return tainted, untainted_locals


def _apply_mutation_taint(
    line: str,
    tainted: dict[str, list[str]],
    untainted_locals: set[str],
) -> None:
    """Checks in-place mutation like `query.append(user_val)` or `list.extend(items)`."""
    mut_m = _MUTATION_CALL_RE.match(line)
    if not mut_m or not line.endswith(")"):
        return
    mut_target = mut_m.group(1)
    mut_arg = line[mut_m.end() : -1]
    mut_idents = [tok for tok in _IDENT_RE.findall(mut_arg) if tok not in _KEYWORDS]
    matched_mut = next((tainted[t] for t in mut_idents if t in tainted), None)
    if matched_mut or any(src in mut_arg for src in _DIRECT_SOURCES):
        origin = matched_mut or [_UNTRUSTED_INPUT]
        existing = tainted.get(mut_target, origin)
        tainted[mut_target] = [*existing, mut_target] if mut_target not in existing else existing
        untainted_locals.discard(mut_target)


def _apply_dict_access_taint(
    lhs: str,
    rhs: str,
    sanitizers: frozenset[str],
    tainted: dict[str, list[str]],
    untainted_locals: set[str],
) -> bool:
    """Handles dictionary access patterns `params['id']` or `params.get('page')`."""
    dict_m = _DICT_BRACKET_RE.search(rhs) or _DICT_GET_RE.search(rhs)
    if not dict_m:
        return False
    dict_var, key_name = dict_m.group(1), dict_m.group(2)
    is_tainted_dict = dict_var in tainted or any(
        src.startswith(f"{dict_var}.") for src in _DIRECT_SOURCES
    )
    if not is_tainted_dict:
        return False
    if key_name.lower() in _SAFE_DICT_KEYS or any(f"{san}(" in rhs for san in sanitizers):
        tainted.pop(lhs, None)
        untainted_locals.add(lhs)
    else:
        origin = tainted.get(dict_var, [dict_var])
        tainted[lhs] = [*origin, f"{dict_var}[{key_name}]", lhs]
        untainted_locals.discard(lhs)
    return True


def _apply_assignment_taint(
    lhs: str,
    op: str,
    rhs: str,
    sanitizers: frozenset[str],
    contracts_map: dict[str, FunctionTaintContract] | None,
    tainted: dict[str, list[str]],
    untainted_locals: set[str],
) -> None:
    """Updates taint state for a single assignment `lhs <op> rhs`."""
    if any(f"{san}(" in rhs or f".{san}(" in rhs for san in sanitizers):
        tainted.pop(lhs, None)
        untainted_locals.add(lhs)
        return

    if _apply_dict_access_taint(lhs, rhs, sanitizers, tainted, untainted_locals):
        return

    if any(src in rhs for src in _DIRECT_SOURCES):
        tainted[lhs] = [lhs]
        untainted_locals.discard(lhs)
        return

    if rhs in tainted:
        tainted[lhs] = [*tainted[rhs], lhs]
        untainted_locals.discard(lhs)
        return

    handled, matched_chain = _eval_helper_call_contract(rhs, contracts_map, tainted)
    if handled:
        if matched_chain:
            tainted[lhs] = [*matched_chain, lhs] if lhs not in matched_chain else matched_chain
            untainted_locals.discard(lhs)
        else:
            tainted.pop(lhs, None)
            untainted_locals.add(lhs)
        return

    rhs_idents = [tok for tok in _IDENT_RE.findall(rhs) if tok not in _KEYWORDS]
    matched_taint = next((tainted[tok] for tok in rhs_idents if tok in tainted), None)
    if matched_taint is not None:
        tainted[lhs] = [*matched_taint, lhs] if lhs not in matched_taint else matched_taint
        untainted_locals.discard(lhs)
    elif op == "=":
        tainted.pop(lhs, None)
        untainted_locals.add(lhs)


def _parse_assignment_line(line: str) -> tuple[str, str, str] | None:
    """Parses `lhs <op> rhs` from a statement line without complex regex backtracking."""
    m = _ASSIGN_OP_RE.search(line)
    if not m:
        return None
    prefix = line[: m.start()].strip()
    if any(ch in prefix for ch in ("(", ")", "=")):
        return None
    rhs = line[m.end() :].strip()
    if not rhs or rhs.startswith("="):
        return None
    return m.group(1), m.group(2), rhs


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
    tainted, untainted_locals = _init_param_taint(params, tainted_param_indices)

    for idx in range(s_idx + 1, stop_idx):
        line = lines[idx].split("#", 1)[0].strip()
        if not line or "==" in line.split("=", 1)[0]:
            continue
        parsed_assign = _parse_assignment_line(line)
        if not parsed_assign:
            _apply_mutation_taint(line, tainted, untainted_locals)
            continue
        lhs, op, rhs = parsed_assign
        _apply_assignment_taint(lhs, op, rhs, sanitizers, contracts_map, tainted, untainted_locals)

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
        content = abs_path.read_text(encoding="utf-8", errors=_ENCODING_ERRORS)
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
        return True, [_UNTRUSTED_INPUT]

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
    callee_path = (project_root / str(callee.get(_KEY_FILE_PATH, ""))).resolve()
    if not callee_path.is_file():
        return False
    try:
        callee_content = callee_path.read_text(encoding="utf-8", errors=_ENCODING_ERRORS)
        callee_contracts = TaintContractManager.analyze_source_code(
            callee_content, str(callee[_KEY_FILE_PATH])
        )
        callee_contract = callee_contracts.get(callee_name)
        return bool(callee_contract and callee_contract.is_sanitizer)
    except Exception:
        return False


def _unpack_call_argument(
    arg_idx: int,
    arg_expr: str,
    callee_param_names: list[str] | None,
) -> tuple[int, str]:
    """Resolves `(target_param_index, value_expression)` for a positional or keyword argument."""
    if "=" in arg_expr and not arg_expr.startswith("="):
        lhs_part, rhs_part = arg_expr.split("=", 1)
        kw_name = lhs_part.strip()
        if _IDENT_RE.fullmatch(kw_name) and not rhs_part.startswith("="):
            val_expr = rhs_part.strip()
            if callee_param_names and kw_name in callee_param_names:
                return callee_param_names.index(kw_name), val_expr
            return arg_idx, val_expr
    return arg_idx, arg_expr


def _resolve_next_tainted_params(
    args: list[str],
    sanitizers: frozenset[str],
    caller_tainted: dict[str, list[str]],
    callee_param_names: list[str] | None = None,
) -> dict[int, list[str]]:
    """Calculates tainted parameters forwarded to callee, supporting positional and keyword args."""
    next_tainted: dict[int, list[str]] = {}
    for arg_idx, arg_expr in enumerate(args):
        target_idx, val_expr = _unpack_call_argument(arg_idx, arg_expr, callee_param_names)
        if any(f"{san}(" in val_expr or f".{san}(" in val_expr for san in sanitizers):
            continue
        if any(src in val_expr for src in _DIRECT_SOURCES):
            next_tainted[target_idx] = [_UNTRUSTED_INPUT]
            continue
        arg_idents = [tok for tok in _IDENT_RE.findall(val_expr) if tok not in _KEYWORDS]
        matched = next((caller_tainted[tok] for tok in arg_idents if tok in caller_tainted), None)
        if matched is not None:
            next_tainted[target_idx] = matched
    return next_tainted


def _scan_line_delimiters(text: str, depth: int, in_quote: str | None) -> tuple[int, str | None]:
    """Advances delimiter depth across a single line of text."""
    for ch in text:
        depth, in_quote, _ = _advance_delimiter_state(ch, depth, in_quote)
        if depth == 0:
            break
    return depth, in_quote


def _extract_call_args_multiline(lines: list[str], start_idx: int, initial_tail: str) -> list[str]:
    """Extracts call argument strings across single- or multi-line function invocations."""
    combined = initial_tail
    depth, in_quote = _scan_line_delimiters(initial_tail, 1, None)

    curr = start_idx + 1
    while depth > 0 and curr < len(lines):
        line = lines[curr].split("#", 1)[0]
        depth, in_quote = _scan_line_delimiters(line, depth, in_quote)
        combined += " " + line
        curr += 1

    return _split_top_level_args(combined)


def _find_call_sites(
    c_lines: list[str], s_idx: int, e_idx: int, callee_name: str
) -> list[tuple[int, str]]:
    """Finds all call sites of `callee_name` within the caller body line range."""
    call_re = re.compile(rf"\b{re.escape(callee_name)}\s*\((.*)$")
    call_sites: list[tuple[int, str]] = []
    for idx in range(s_idx, e_idx):
        code_part = c_lines[idx].split("#", 1)[0]
        m = call_re.search(code_part)
        if m and not code_part.lstrip().startswith(("def ", "function ", "func ", "fn ")):
            call_sites.append((idx, m.group(1)))
    return call_sites


def _load_callee_params(project_root: Path, callee: dict[str, Any]) -> list[str] | None:
    """Reads the callee source file and extracts its parameter list."""
    callee_path = (project_root / str(callee.get(_KEY_FILE_PATH, ""))).resolve()
    if not callee_path.is_file():
        return None
    try:
        cal_lines = callee_path.read_text(encoding="utf-8", errors=_ENCODING_ERRORS).splitlines()
        c_s_idx = max(0, int(callee[_KEY_START_LINE]) - 1)
        callee_header = " ".join(cal_lines[c_s_idx : min(len(cal_lines), c_s_idx + 4)])
        return _extract_params(callee_header)
    except Exception:
        return None


def _merge_taint_chains(target: dict[int, list[str]], source: dict[int, list[str]]) -> None:
    """Merges parameter taint chains from a single call site into `target`."""
    for p_idx, chain in source.items():
        if p_idx not in target:
            target[p_idx] = list(chain)
            continue
        existing = target[p_idx]
        for var in chain:
            if var not in existing:
                existing.append(var)


def _evaluate_hop_taint(
    project_root: Path,
    caller: dict[str, Any],
    callee: dict[str, Any],
    sanitizers: frozenset[str],
    tainted_params: dict[int, list[str]] | None,
) -> tuple[str, dict[int, list[str]] | None]:
    """Evaluates taint propagation from `caller` to `callee` across all call sites."""
    callee_name = str(callee["name"]).split(".")[-1]
    c_path = (project_root / str(caller[_KEY_FILE_PATH])).resolve()
    try:
        c_lines = c_path.read_text(encoding="utf-8", errors=_ENCODING_ERRORS).splitlines()
    except OSError:
        return "continue", None

    s_idx = max(0, int(caller[_KEY_START_LINE]) - 1)
    e_idx = min(len(c_lines), int(caller[_KEY_END_LINE]))
    call_sites = _find_call_sites(c_lines, s_idx, e_idx, callee_name)
    if not call_sites:
        return "continue", None

    if _is_callee_sanitizer(callee, project_root, callee_name):
        return "sanitized_callee", None

    caller_contracts = TaintContractManager.analyze_source_code(
        "\n".join(c_lines), str(caller[_KEY_FILE_PATH])
    )
    callee_params = _load_callee_params(project_root, callee)
    aggregated: dict[int, list[str]] = {}

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
        if args:
            site_tainted = _resolve_next_tainted_params(
                args, sanitizers, caller_tainted, callee_params
            )
            _merge_taint_chains(aggregated, site_tainted)

    if not aggregated:
        return "untainted_call_arg", None
    return "ok", aggregated


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
    hops = [
        n
        for n in path_nodes
        if n.get("kind") in ("function", "method")
        and n.get(_KEY_FILE_PATH)
        and n.get(_KEY_START_LINE)
        and n.get(_KEY_END_LINE)
    ]
    if not hops:
        return True, [], "tainted"

    tainted_params: dict[int, list[str]] | None = None

    for i in range(len(hops) - 1):
        status, next_params = _evaluate_hop_taint(
            project_root, hops[i], hops[i + 1], sanitizers, tainted_params
        )
        if status in ("sanitized_callee", "untainted_call_arg"):
            return False, [], status
        tainted_params = next_params

    sink_hop = hops[-1]
    is_tainted, chain = verify_sink_taint_flow(
        project_root=project_root,
        file_path=sink_file_path,
        func_start_line=int(sink_hop[_KEY_START_LINE]),
        func_end_line=int(sink_hop[_KEY_END_LINE]),
        sink_line=sink_line,
        sanitizers=sanitizers,
        tainted_param_indices=tainted_params,
    )
    return is_tainted, chain, ("tainted" if is_tainted else "untainted_local")
