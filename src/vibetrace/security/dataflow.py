"""Intra-procedural Def-Use variable taint analyzer for sink argument provenance."""

from __future__ import annotations

import re
from pathlib import Path

_IDENT_RE = re.compile(r"\b([A-Za-z_][A-Za-z0-9_]*)\b")
_ASSIGN_RE = re.compile(
    r"^\s*(?:(?:const|let|var)\s+)?([A-Za-z_][A-Za-z0-9_]*)\s*(?::[^=]+)?=\s*(.+)$"
)
_FUNC_PARAMS_RE = re.compile(r"(?:def|function|func|fn)\s+[A-Za-z0-9_]+\s*\(([^)]*)\)")
_DIRECT_SOURCES = ("request.", "req.", "sys.argv", "os.environ", "input(", "process.argv")
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


def verify_sink_taint_flow(
    project_root: Path,
    file_path: str,
    func_start_line: int,
    func_end_line: int,
    sink_line: int,
    sanitizers: frozenset[str],
) -> tuple[bool, list[str]]:
    """Checks whether untrusted parameters or sources flow into the sink call on `sink_line`.

    Returns `(is_tainted, variable_chain)`. If a function only passes untainted local constants
    to the sink (e.g. `cmd = 'ls'; os.system(cmd)`), returns `(False, [])`.
    """
    abs_path = (project_root / file_path).resolve()
    try:
        lines = abs_path.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return True, []

    s_idx = max(0, func_start_line - 1)
    e_idx = min(len(lines), func_end_line)
    k_idx = max(s_idx, min(len(lines) - 1, sink_line - 1))
    if s_idx >= len(lines):
        return True, []

    # 1. Seed taint from function parameters
    header_slice = " ".join(lines[s_idx : min(e_idx, s_idx + 4)])
    params = _extract_params(header_slice)
    tainted: dict[str, list[str]] = {p: [p] for p in params}
    untainted_locals: set[str] = set()

    # 2. Propagate Def-Use assignments from function start up to sink line
    for idx in range(s_idx + 1, k_idx):
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

        matched_taint = next((tainted[tok] for tok in rhs_idents if tok in tainted), None)
        if matched_taint is not None:
            tainted[lhs] = [*matched_taint, lhs] if lhs not in matched_taint else matched_taint
            untainted_locals.discard(lhs)
        else:
            tainted.pop(lhs, None)
            untainted_locals.add(lhs)

    # 3. Inspect sink line argument provenance
    sink_text = lines[k_idx].split("#", 1)[0]
    if any(src in sink_text for src in _DIRECT_SOURCES):
        return True, ["untrusted_input"]

    # Extract arguments inside the sink call parentheses
    arg_part = sink_text.split("(", 1)[1] if "(" in sink_text else sink_text
    sink_idents = [tok for tok in _IDENT_RE.findall(arg_part) if tok not in _KEYWORDS]

    for tok in sink_idents:
        if tok in tainted:
            return True, tainted[tok]

    # If the sink only references known untainted local variables (or has no tainted vars at all)
    if untainted_locals and any(tok in untainted_locals for tok in sink_idents):
        return False, []
    if params and not any(tok in tainted for tok in sink_idents):
        return False, []

    return True, []
