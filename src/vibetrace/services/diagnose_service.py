"""Consolidated 360° Symbol Diagnosis Service for AI Agents.

Aggregates source slicing, callee contracts, incoming callers with exact line coordinates,
transitive blast radius, linked architectural documentation, and sibling test reachability
into a single, bounded payload in one round-trip.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field

from vibetrace.docs.symbol_linker import estimate_tokens
from vibetrace.indexer.db import IndexerDB
from vibetrace.services.caller_guard import CallerGuardService
from vibetrace.services.coverage_service import _is_test_symbol_or_file
from vibetrace.services.trace_service import get_code_slice


class SymbolDiagnosisReport(BaseModel):
    """Unified 360° diagnostic report for an AST symbol."""

    found: bool = True
    symbol: str
    file_path: str = ""
    start_line: int = 1
    end_line: int = 1
    code_slice: str = ""
    callee_contracts: list[dict[str, Any]] = Field(default_factory=list)
    callers: list[dict[str, Any]] = Field(default_factory=list)
    blast_radius: dict[str, list[str]] = Field(default_factory=dict)
    linked_docs: list[dict[str, Any]] = Field(default_factory=list)
    exercising_tests: list[str] = Field(default_factory=list)
    safety_advisory: str = ""
    token_count: int = 0


class DiagnoseService:
    """Consolidates symbol diagnostics across 5 dimensions in 1 pass."""

    @classmethod
    def diagnose_symbol(
        cls,
        db: IndexerDB,
        project_root: Path,
        symbol_name: str,
        max_depth: int = 2,
        include_code: bool = True,
    ) -> SymbolDiagnosisReport:
        clean_name = symbol_name.strip().removesuffix("()")

        # 1. Source slice, callee contracts, and linked documentation
        slice_res = get_code_slice(
            db=db,
            root=project_root,
            symbol_name=clean_name,
            include_callees=True,
            include_docs=True,
            max_doc_tokens=300,
        )

        if not slice_res.found:
            return SymbolDiagnosisReport(
                found=False,
                symbol=symbol_name,
                safety_advisory=f"Symbol '{symbol_name}' was not found in the AST index.",
            )

        # 2. Incoming callers and call sites with exact line numbers
        guard = CallerGuardService.get_caller_guard(
            db=db,
            root=project_root,
            symbol_name=clean_name,
            file_path=slice_res.file_path,
        )

        # 3. Transitive blast radius (L1 direct, L2 secondary callers)
        blast_l1: list[str] = []
        blast_l2: list[str] = []
        with db._get_conn() as conn:
            # Query incoming callers up to 2 hops using exact node IDs when available
            target_id = slice_res.node_id
            if target_id:
                l1_rows = conn.execute(
                    "SELECT DISTINCT n.id, n.name FROM edges e "
                    "JOIN nodes n ON e.from_node_id = n.id "
                    "WHERE e.to_node_id = ? AND e.relation_type = 'calls'",
                    (target_id,),
                ).fetchall()
            else:
                l1_rows = conn.execute(
                    "SELECT DISTINCT n.id, n.name FROM edges e "
                    "JOIN nodes n ON e.from_node_id = n.id "
                    "JOIN nodes tgt ON e.to_node_id = tgt.id "
                    "WHERE (tgt.name = ? OR tgt.qualified_name = ?) AND e.relation_type = 'calls'",
                    (clean_name, clean_name),
                ).fetchall()

            l1_ids = {r[0] for r in l1_rows if r[0]}
            l1_names = {r[1] for r in l1_rows if r[1]}
            blast_l1 = sorted(l1_names)

            if max_depth >= 2 and l1_ids:
                placeholders = ",".join(["?"] * len(l1_ids))
                l2_rows = conn.execute(
                    "SELECT DISTINCT n.name FROM edges e "
                    "JOIN nodes n ON e.from_node_id = n.id "
                    f"WHERE e.to_node_id IN ({placeholders}) "
                    "AND e.relation_type = 'calls'",
                    tuple(l1_ids),
                ).fetchall()
                blast_l2 = sorted(
                    {r[0] for r in l2_rows if r[0] and r[0] not in l1_names and r[0] != clean_name}
                )

            # 4. Sibling test fixture reachability
            if target_id:
                all_callers = conn.execute(
                    "SELECT DISTINCT n.name, n.file_path FROM edges e "
                    "JOIN nodes n ON e.from_node_id = n.id "
                    "WHERE e.to_node_id = ?",
                    (target_id,),
                ).fetchall()
            else:
                all_callers = conn.execute(
                    "SELECT DISTINCT n.name, n.file_path FROM edges e "
                    "JOIN nodes n ON e.from_node_id = n.id "
                    "JOIN nodes tgt ON e.to_node_id = tgt.id "
                    "WHERE (tgt.name = ? OR tgt.qualified_name = ?)",
                    (clean_name, clean_name),
                ).fetchall()

        exercising_tests: list[str] = []
        for c_name, c_path in all_callers:
            clean_fp = (c_path or "").replace("\\", "/")
            if _is_test_symbol_or_file(c_name or "", clean_fp):
                exercising_tests.append(f"{c_name} ({clean_fp})")

        callees_list = [
            {
                "callee_name": c.name,
                "file_path": c.file_path,
                "call_line": c.call_line,
                "signature": c.signature,
            }
            for c in slice_res.callees
        ]

        callers_list = [
            {
                "caller_name": c.caller_name,
                "file_path": c.caller_file,
                "call_line": c.call_line,
                "relation_type": c.relation_type,
            }
            for c in guard.callers
        ]

        linked_docs_list = [
            {
                "doc_path": getattr(d, "doc_path", ""),
                "heading": getattr(d, "heading", ""),
                "heading_path": getattr(d, "heading_path", ""),
                "token_count": getattr(d, "token_count", 0),
                "summary": str(getattr(d, "content", "")).strip().split("\n")[0][:120],
            }
            for d in slice_res.linked_docs
        ]

        code_text = slice_res.source_code if include_code else ""
        doc_tokens = sum(int(getattr(d, "token_count", 0)) for d in slice_res.linked_docs)
        token_count = estimate_tokens(code_text) + doc_tokens + len(callers_list) * 15

        return SymbolDiagnosisReport(
            found=True,
            symbol=clean_name,
            file_path=slice_res.file_path or "",
            start_line=slice_res.start_line or 1,
            end_line=slice_res.end_line or 1,
            code_slice=code_text,
            callee_contracts=callees_list,
            callers=callers_list,
            blast_radius={"L1_direct": blast_l1, "L2_secondary": blast_l2},
            linked_docs=linked_docs_list,
            exercising_tests=sorted(exercising_tests),
            safety_advisory=guard.safety_advisory,
            token_count=token_count,
        )


def format_diagnosis_md(report: SymbolDiagnosisReport) -> str:
    """Format diagnosis report into compact, structured Markdown for AI agents."""
    if not report.found:
        return f"❌ {report.safety_advisory}"

    loc = f"`{report.file_path}:{report.start_line}..{report.end_line}`"
    lines = [
        f"# 🔬 360° Symbol Diagnosis: `{report.symbol}` ({loc})\n",
        f"- **Estimated Token Weight:** ~{report.token_count} tokens",
        f"- **Safety Advisory:** {report.safety_advisory}\n",
    ]

    # 1. Incoming Callers
    lines.append(f"### 🛡️ Incoming Call Sites ({len(report.callers)})")
    if report.callers:
        for c in report.callers:
            line_str = f":L{c['call_line']}" if c.get("call_line") else ""
            lines.append(f"- `{c['caller_name']}` in `{c['file_path']}{line_str}`")
    else:
        lines.append("- *No incoming callers detected (leaf or external entry point).*")
    lines.append("")

    # 2. Transitive Blast Radius
    l1 = report.blast_radius.get("L1_direct", [])
    l2 = report.blast_radius.get("L2_secondary", [])
    lines.append(f"### 💥 Blast Radius (L1 Direct: {len(l1)}, L2 Secondary: {len(l2)})")
    if l1:
        lines.append(
            f"- **Direct L1:** `{', '.join(l1[:8])}`"
            + (f" (+{len(l1) - 8} more)" if len(l1) > 8 else "")
        )
    if l2:
        lines.append(
            f"- **Secondary L2:** `{', '.join(l2[:8])}`"
            + (f" (+{len(l2) - 8} more)" if len(l2) > 8 else "")
        )
    if not l1 and not l2:
        lines.append("- *Zero downstream caller blast radius.*")
    lines.append("")

    # 3. Test Fixtures
    lines.append(f"### 🧪 Sibling Test Coverage ({len(report.exercising_tests)})")
    if report.exercising_tests:
        for t in report.exercising_tests[:6]:
            lines.append(f"- `{t}`")
        if len(report.exercising_tests) > 6:
            lines.append(f"- *... and {len(report.exercising_tests) - 6} more test fixtures.*")
    else:
        lines.append("- ⚠️ *No automated tests currently exercise this symbol.*")
    lines.append("")

    # 4. Architectural Specs
    if report.linked_docs:
        lines.append(f"### 📚 Linked Architectural Specifications ({len(report.linked_docs)})")
        for d in report.linked_docs:
            lines.append(f"- **{d['heading']}** (`{d['doc_path']}`): {d['summary']}")
        lines.append("")

    # 5. Outgoing Callee Contracts
    if report.callee_contracts:
        lines.append(f"### 📞 Outgoing Callee Contracts ({len(report.callee_contracts)})")
        for cal in report.callee_contracts[:8]:
            tag = "[Internal]" if cal.get("is_internal") else "[External/Stdlib]"
            line_str = f":L{cal['call_line']}" if cal.get("call_line") else ""
            lines.append(f"- `{cal['callee_name']}` in `{cal['file_path']}{line_str}` {tag}")
        lines.append("")

    # 6. Source Slice
    if report.code_slice:
        lines.append(f"### ✂️ Source Code Slice ({report.start_line}..{report.end_line})")
        lines.append(f"```python\n{report.code_slice}\n```")

    return "\n".join(lines)
