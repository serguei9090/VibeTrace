"""Caller Guard service for pre-flight refactoring safety and blast-radius prevention."""

from __future__ import annotations

from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field

from vibetrace.indexer.db import IndexerDB
from vibetrace.services.trace_service import read_file_lines


class CallerSite(BaseModel):
    """Details of a single incoming caller invocation."""

    model_config = ConfigDict(str_strip_whitespace=True)

    caller_name: str
    caller_file: str
    call_line: int | None = None
    relation_type: str = "calls"
    signature: str | None = None
    call_snippet: str | None = None


class CallerGuardReport(BaseModel):
    """Pre-flight caller inspection report to prevent breaking sibling callers."""

    model_config = ConfigDict(str_strip_whitespace=True)

    found: bool
    symbol: str
    file_path: str | None = None
    target_line: int | None = None
    target_signature: str | None = None
    total_callers: int = 0
    callers: list[CallerSite] = Field(default_factory=list)
    safety_advisory: str = ""


class CallerGuardService:
    """Inspects all incoming callers of a symbol before code modification."""

    @classmethod
    def get_caller_guard(
        cls,
        db: IndexerDB,
        root: Path,
        symbol_name: str,
        file_path: str | None = None,
    ) -> CallerGuardReport:
        clean_name = symbol_name.strip().removesuffix("()")
        norm_file: str | None = None
        if file_path:
            p = Path(file_path)
            if not p.is_absolute():
                p = root / p
            try:
                norm_file = str(p.resolve().relative_to(root.resolve())).replace("\\", "/")
            except ValueError:
                norm_file = file_path.replace("\\", "/")

        matches = db.find_nodes_by_name(clean_name, file_path=norm_file)
        if not matches and not norm_file:
            matches = db.search_ast_exact_or_prefix(clean_name, limit=5)

        if not matches:
            return CallerGuardReport(
                found=False,
                symbol=symbol_name,
                safety_advisory=f"Symbol '{symbol_name}' was not found in the AST graph.",
            )

        target = matches[0]
        nid = target["id"]
        rel_file = target["file_path"]
        s_line = target.get("start_line")
        sig = target.get("signature")

        # Query incoming callers
        neighbors = db.get_neighbors(nid, "up")
        caller_sites: list[CallerSite] = []
        seen_sites: set[tuple[str, int | None]] = set()

        for row in neighbors:
            rel = row.get("relation_type", "calls")
            if rel not in ("calls", "routes"):
                continue

            c_file = row.get("file_path", "")
            c_line = row.get("call_line")
            site_key = (c_file, c_line)
            if site_key in seen_sites:
                continue
            seen_sites.add(site_key)

            snippet: str | None = None
            if c_line and c_file:
                try:
                    snip_text, _, _ = read_file_lines(root, c_file, c_line, c_line)
                    snippet = snip_text.strip()
                except Exception:
                    snippet = None

            caller_sites.append(
                CallerSite(
                    caller_name=row.get("name") or row.get("qualified_name") or "unknown",
                    caller_file=c_file,
                    call_line=c_line,
                    relation_type=rel,
                    signature=row.get("signature"),
                    call_snippet=snippet,
                )
            )

        # Sort callers deterministically by file and line
        caller_sites.sort(key=lambda c: (c.caller_file, c.call_line or 0))

        distinct_files = len({c.caller_file for c in caller_sites})
        if caller_sites:
            advisory = (
                f"GUARD ALERT: Modifying signature of '{clean_name}' will affect "
                f"{len(caller_sites)} call site(s) across {distinct_files} file(s). "
                f"Ensure parameters, return types, and call sites are updated synchronously."
            )
        else:
            advisory = (
                f"GUARD OK: Symbol '{clean_name}' has 0 recorded callers in this codebase. "
                "Safe for local modifications or entry-point refactoring."
            )

        return CallerGuardReport(
            found=True,
            symbol=clean_name,
            file_path=rel_file,
            target_line=s_line,
            target_signature=sig,
            total_callers=len(caller_sites),
            callers=caller_sites,
            safety_advisory=advisory,
        )


def format_caller_guard_md(report: CallerGuardReport) -> str:
    """Format CallerGuardReport as structured markdown for AI agents."""
    if not report.found:
        return f"## Caller Guard: `{report.symbol}`\n\n{report.safety_advisory}"

    lines = [f"## Caller Guard Report: `{report.symbol}`", ""]
    loc_str = f"`{report.file_path}:L{report.target_line}`" if report.file_path else ""
    sig_str = f" `{report.target_signature}`" if report.target_signature else ""
    lines.append(f"**Target Definition:** {loc_str}{sig_str}")
    lines.append(f"**Status:** {report.safety_advisory}")
    lines.append("")

    if not report.callers:
        lines.append("No active callers recorded in AST call graph.")
        return "\n".join(lines)

    lines.append(f"### Incoming Call Sites ({report.total_callers}):")
    for idx, c in enumerate(report.callers, start=1):
        ln_str = f":L{c.call_line}" if c.call_line else ""
        lines.append(f"{idx}. **`{c.caller_name}`** in `{c.caller_file}{ln_str}`")
        if c.call_snippet:
            lines.append(f"   ```text\n   {c.call_snippet}\n   ```")
    return "\n".join(lines)
