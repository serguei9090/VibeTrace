"""Caller Guard service for pre-flight refactoring safety and blast-radius prevention."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

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
    alternative_candidates: list[str] = Field(default_factory=list)
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

        # Disambiguation: if multiple candidates match, rank by incoming caller count / centrality
        alt_candidates: list[str] = []
        if len(matches) > 1 and not norm_file:
            candidates_with_score: list[tuple[int, dict[str, Any]]] = []
            for m in matches:
                c_count = len(
                    [
                        e
                        for e in db.get_neighbors(m["id"], "up")
                        if e.get("relation_type") in ("calls", "routes")
                    ]
                )
                candidates_with_score.append((c_count, m))
            candidates_with_score.sort(
                key=lambda x: (x[0], x[1].get("centrality", 0.0)), reverse=True
            )
            target = candidates_with_score[0][1]
            alt_candidates = [
                f"{c[1]['file_path']}:L{c[1].get('start_line', 1)} ({c[0]} callers)"
                for c in candidates_with_score[1:]
            ]
        else:
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

        if alt_candidates:
            advisory += (
                f" [Disambiguation: Selected '{rel_file}'. {len(alt_candidates)} alternate "
                f"symbol(s) found: {', '.join(alt_candidates)}]"
            )

        return CallerGuardReport(
            found=True,
            symbol=clean_name,
            file_path=rel_file,
            target_line=s_line,
            target_signature=sig,
            total_callers=len(caller_sites),
            callers=caller_sites,
            alternative_candidates=alt_candidates,
            safety_advisory=advisory,
        )

    @classmethod
    def get_staged_caller_guard(
        cls,
        db: IndexerDB,
        root: Path,
        diff_text: str | None = None,
    ) -> list[CallerGuardReport]:
        """Inspect all symbol definitions modified in staged git diff."""
        if diff_text is None:
            import subprocess

            try:
                proc = subprocess.run(
                    ["git", "diff", "--cached", "-U0"],
                    cwd=str(root),
                    capture_output=True,
                    text=True,
                    check=False,
                )
                diff_text = proc.stdout
            except Exception:
                diff_text = ""

        if not diff_text or not diff_text.strip():
            return []

        file_ranges = parse_git_diff_line_ranges(diff_text)
        reports: list[CallerGuardReport] = []
        seen_nodes: set[str] = set()

        for file_path, ranges in file_ranges.items():
            norm_file = file_path.replace("\\", "/")
            nodes = db.get_nodes_in_file(norm_file)
            for n in nodes:
                nid = n["id"]
                if nid in seen_nodes or n.get("kind") in ("module", "file"):
                    continue
                s_line = n.get("start_line") or 1
                e_line = n.get("end_line") or s_line
                overlaps = any(s_line <= r_end and e_line >= r_start for r_start, r_end in ranges)
                if overlaps:
                    seen_nodes.add(nid)
                    report = cls.get_caller_guard(
                        db, root, symbol_name=n["name"], file_path=norm_file
                    )
                    if report.found:
                        reports.append(report)

        return reports


def parse_git_diff_line_ranges(diff_text: str) -> dict[str, list[tuple[int, int]]]:
    """Parse unified git diff output into modified line ranges per file."""
    file_ranges: dict[str, list[tuple[int, int]]] = {}
    current_file: str | None = None

    for raw_line in diff_text.splitlines():
        line = raw_line.strip()
        if line.startswith("diff --git a/"):
            parts = line.split(" b/", 1)
            if len(parts) == 2:
                current_file = parts[1].strip()
        elif line.startswith("@@ ") and current_file:
            m = re.search(r"@@ -\d+(?:,\d+)? \+(\d+)(?:,(\d+))? @@", line)
            if m:
                start = int(m.group(1))
                count = int(m.group(2)) if m.group(2) else 1
                end = start + max(1, count) - 1
                file_ranges.setdefault(current_file, []).append((start, end))

    return file_ranges


def format_staged_guard_md(reports: list[CallerGuardReport]) -> str:
    """Format staged caller guard reports as markdown for git pre-commit."""
    if not reports:
        return (
            "## 🛡️ Pre-Commit Caller Guard Check\n\n"
            "No modified symbol definitions detected in staged changes."
        )

    total_callers = sum(r.total_callers for r in reports)
    files_affected = len({c.caller_file for r in reports for c in r.callers})
    lines = [
        "## 🛡️ Pre-Commit Caller Guard Check",
        (
            f"Inspected {len(reports)} modified symbol definition(s). "
            f"Total incoming callers affected: {total_callers} across {files_affected} file(s)."
        ),
        "",
    ]

    for r in reports:
        loc = f"`{r.file_path}:L{r.target_line}`" if r.file_path else ""
        icon = "⚠️" if r.total_callers > 0 else "✅"
        lines.append(f"### {icon} `{r.symbol}` ({loc})")
        lines.append(f"**Status:** {r.safety_advisory}")
        if r.callers:
            lines.append(f"**Incoming Callers ({r.total_callers}):**")
            for c in r.callers[:5]:
                ln_str = f":L{c.call_line}" if c.call_line else ""
                lines.append(f"- `{c.caller_name}` ({c.caller_file}{ln_str})")
        lines.append("")

    return "\n".join(lines).strip()



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

    if report.alternative_candidates:
        lines.append("")
        lines.append(f"### Alternate Symbols Named `{report.symbol}`:")
        for alt in report.alternative_candidates:
            lines.append(f"- `{alt}`")

    return "\n".join(lines)
