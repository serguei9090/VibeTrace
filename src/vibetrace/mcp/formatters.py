"""Markdown and JSON formatting utilities for VibeTrace MCP & CLI tools."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from vibetrace.services.graph_service import CodeSliceResult, TracePathResult, safe_str
from vibetrace.services.hybrid_search import SearchResult

_EXT_LANG_MAP: dict[str, str] = {
    ".py": "python",
    ".ts": "typescript",
    ".tsx": "tsx",
    ".js": "javascript",
    ".jsx": "jsx",
    ".go": "go",
    ".rs": "rust",
    ".java": "java",
    ".c": "c",
    ".h": "c",
    ".cpp": "cpp",
    ".cc": "cpp",
    ".cxx": "cpp",
    ".hpp": "cpp",
    ".cs": "csharp",
    ".rb": "ruby",
    ".php": "php",
    ".html": "html",
    ".css": "css",
    ".json": "json",
    ".sql": "sql",
}


def format_structural_summary_md(
    norm_path: str, total: int, rows: list[dict[str, Any]], limit: int, offset: int
) -> str:
    end_idx = min(offset + len(rows), total)
    header = f"Structure for `{norm_path}` (showing {offset + 1}-{end_idx} of {total} nodes):\n"
    summary = [header]

    for row in rows:
        icon = "[f]"
        if row["kind"] == "class":
            icon = "[c]"
        elif row["kind"] == "module":
            icon = "[m]"

        vis_mark = ""
        if row["visibility"] == "private":
            vis_mark = " 🔒"
        elif row["visibility"] == "exported":
            vis_mark = " 🌐"

        decorators = ""
        if row["decorators"]:
            decs = (
                json.loads(row["decorators"])
                if isinstance(row["decorators"], str)
                else (row["decorators"] or [])
            )
            for d in decs:
                decorators += f"\n  {d}"

        info = f"- {icon} **{row['name']}**{vis_mark}"
        if row["signature"]:
            info += f" `{row['signature']}`"
        info += f" (L{row['start_line']}-{row['end_line']})"
        if decorators:
            info += decorators
        summary.append(info)

    if end_idx < total:
        remaining = total - end_idx
        next_offset = offset + limit
        summary.append(
            f"\n... {remaining} more nodes available (use offset={next_offset} to see more)"
        )

    return "\n".join(summary)


def format_semantic_search_md(
    query: str, results: list[SearchResult], doc_chunks: list[Any] | None = None
) -> str:
    if doc_chunks:
        title = f"## Unified Search Results: `{query}`"
    else:
        title = f"## Hybrid RRF Search: `{query}`"
    lines = [title]

    if doc_chunks:
        lines.append("")
        lines.append("### 📚 Documentation Context")
        for idx, dc in enumerate(doc_chunks, start=1):
            scope = getattr(dc, "scope", "local").upper()
            cat = getattr(dc, "catalog", "")
            fpath = getattr(dc, "file_path", "")
            hpath = getattr(dc, "heading_path", "")
            s_line = getattr(dc, "start_line", 1)
            e_line = getattr(dc, "end_line", 1)
            lines.append(f"{idx}. **[{scope}:{cat}]** `{fpath}` (L{s_line}-L{e_line}) | `{hpath}`")
            first_content = next(
                (ln.strip() for ln in getattr(dc, "content", "").splitlines() if ln.strip()), ""
            )
            if first_content:
                lines.append(f"   - {first_content[:140]}")
        lines.append("")
        lines.append("### 💻 Code Symbols")

    if not results:
        lines.append("No matching symbols found.")
        return "\n".join(lines)

    for idx, hit in enumerate(results, start=1):
        sig = f" `{hit.signature}`" if hit.signature else ""
        span = f":L{hit.start_line}-{hit.end_line}" if hit.start_line else ""
        streams = ", ".join(hit.match_sources)
        cent_str = f" | cent={hit.centrality:.2f}" if hit.centrality > 0 else ""
        lines.append(
            f"{idx}. **`{hit.qualified_name}`** ({hit.kind}){sig} - "
            f"`{hit.file_path}{span}` `[RRF={hit.rrf_score:.4f} | {streams}{cent_str}]`"
        )
        # Whitespace-only docstrings are truthy but have no lines to show.
        first_line = next(
            (ln.strip() for ln in (hit.docstring or "").splitlines() if ln.strip()), ""
        )
        if first_line:
            lines.append(f"   - {first_line[:120]}")
    return "\n".join(lines)


def format_trace_path_md(result: TracePathResult) -> str:
    lines = [f"## Execution Tracepath: `{result.from_symbol}` -> `{result.to_symbol}`"]
    if not result.found:
        lines.append("No execution path found within the requested depth.")
        return "\n".join(lines)

    if not result.hops:
        lines.append(f"Start and target resolve to the same symbol (`{result.from_symbol}`).")
        return "\n".join(lines)

    for path_idx, path in enumerate(result.paths[:3], start=1):
        if len(result.paths) > 1:
            lines.append(f"\n### Path {path_idx} ({len(path)} hops)")
        for hop in path:
            call_ln = f":L{hop.call_line}" if hop.call_line else ""
            to_ln = f":L{hop.to_node.start_line}" if hop.to_node.start_line is not None else ""
            lines.append(
                f"{hop.step_index}. **`{hop.from_node.qualified_name}`** "
                f"(`{hop.from_node.file_path}{call_ln}`) "
                f"{safe_str('→')} [{hop.relation_type}] "
                f"**`{hop.to_node.qualified_name}`** "
                f"(`{hop.to_node.file_path}{to_ln}`)"
            )
    return "\n".join(lines)


def format_code_slice_md(result: CodeSliceResult) -> str:
    if not result.found:
        return f"Symbol '{result.name}' not found in index."

    sig = f"`{result.signature}`" if result.signature else ""
    if result.start_line is not None and result.end_line is not None:
        loc = f":L{result.start_line}-{result.end_line}"
    elif result.start_line is not None:
        loc = f":L{result.start_line}"
    else:
        loc = ""
    lines = [
        f"## Code Slice: `{result.qualified_name}` ({result.kind or 'symbol'}) {sig}",
        f"**Location**: `{result.file_path}{loc}`",
    ]
    if result.docstring:
        lines.append(f"**Docstring**: {result.docstring}")

    ext = Path(result.file_path).suffix.lower() if result.file_path else ""
    fence_lang = _EXT_LANG_MAP.get(ext, "python" if ext == ".py" else "")

    lines.append(f"\n```{fence_lang}")
    lines.append(result.source_code)
    lines.append("```")

    if result.callees:
        lines.append("\n### Outgoing Callee Contracts")
        for c in result.callees:
            c_sig = f" `{c.signature}`" if c.signature else ""
            call_at = f" (called at L{c.call_line})" if c.call_line else ""
            lines.append(
                f"- **`{c.qualified_name}`**{c_sig} in `{c.file_path}:L{c.start_line}`{call_at}"
            )
    return "\n".join(lines)


def format_references_md(symbol: str, summaries: list[dict[str, Any]]) -> str:
    lines = [f"## References to `{symbol}`"]
    for t in summaries:
        target_desc = f"`{t['name']}` from `{t['file_path']}`:L{t['start_line']}"
        refs = t["references"]
        if refs:
            lines.append(f"\n### Usages of {target_desc} ({len(refs)} references)")
            for ref in refs:
                call_ln = ref.get("call_line") or ref.get("start_line", "?")
                rel = ref.get("relation_type", "reference")
                lines.append(
                    f"- Used by `{ref['name']}` in `{ref['file_path']}` (L{call_ln}) [{rel}]"
                )
        else:
            lines.append(f"\n### Usages of {target_desc}\n- No direct callers found.")
    return "\n".join(lines)


def format_dependencies_md(
    norm_path: str,
    internal: list[dict[str, Any]],
    third_party: list[dict[str, Any]],
    stdlib: list[dict[str, Any]],
    dependents: list[dict[str, Any]] | None = None,
    circular: list[dict[str, Any]] | None = None,
    reverse_only: bool = False,
) -> str:
    lines = [f"## Dependencies for `{norm_path}`"]

    if circular:
        lines.append(f"\n### ⚠️ Circular Dependency Alert ({len(circular)})")
        for c in circular:
            lines.append(f"- {c['message']}")

    if not reverse_only:
        if not (internal or third_party or stdlib):
            lines.append("No outbound imports detected or file not indexed.")
        for group, title in (
            (internal, "Internal Project Modules"),
            (third_party, "Third-Party Dependencies"),
            (stdlib, "Standard Library"),
        ):
            if group:
                lines.append(f"\n### {title} ({len(group)})")
                lines.extend(
                    f"- `{dep['name']}`"
                    + (f" ({dep['file_path']})" if title.startswith("Internal") else "")
                    for dep in group
                )

    if dependents is not None:
        lines.append(f"\n### Inbound Dependents ({len(dependents)} files import this module)")
        if not dependents:
            lines.append("  (no project files import this module)")
        else:
            for dep in dependents:
                syms_str = f" [imports: {', '.join(dep['symbols'])}]" if dep.get("symbols") else ""
                lines.append(f"- `{dep['file_path']}`{syms_str}")

    return "\n".join(lines)


def format_signature_search_md(pattern: str, rows: list[dict[str, Any]]) -> str:
    lines = [f"## Signature Search: `{pattern}`"]
    if not rows:
        lines.append("No matches found.")
    else:
        for row in rows:
            loc = f":L{row['start_line']}" if row.get("start_line") is not None else ""
            sig = f": `{row.get('signature')}`" if row.get("signature") else ""
            lines.append(f"- **`{row['name']}`**{sig}\n  - In `{row['file_path']}{loc}`")
    return "\n".join(lines)


def format_impact_analysis_md(
    normalized_path: str,
    total_impact: int,
    impacts_by_level: dict[int, list[str]],
) -> str:
    """Format 3-level blast radius impact analysis as GitHub markdown."""
    output = [f"## Impact Analysis for `{normalized_path}`"]
    if total_impact == 0:
        output.append("✅ No external dependencies found. Safe to refactor internally.")
    else:
        output.append(f"**Total Impact**: {total_impact} components affected up to 3 levels.\n")
        for lvl, title in (
            (1, "### Level 1: Direct Impact"),
            (2, "### Level 2: Secondary Impact (Ripple Effect)"),
            (3, "### Level 3: Deep Impact"),
        ):
            if impacts_by_level.get(lvl):
                output.append(title)
                output.extend(sorted(set(impacts_by_level[lvl])))
                output.append("")
    return "\n".join(output)


def format_saved_traces_md(traces: list[Any]) -> str:
    """Format saved architectural trace queries as GitHub markdown."""
    if not traces:
        return "No saved architectural traces found."
    lines = ["## Saved Architectural Traces"]
    for t in traces:
        name = getattr(t, "name", None) or (t.get("name") if isinstance(t, dict) else "")
        desc = getattr(t, "description", None) or (
            t.get("description") if isinstance(t, dict) else ""
        )
        from_sym = getattr(t, "from_symbol", None) or (
            t.get("from_symbol") if isinstance(t, dict) else ""
        )
        to_sym = getattr(t, "to_symbol", None) or (
            t.get("to_symbol") if isinstance(t, dict) else ""
        )
        depth = getattr(t, "max_depth", None) or (
            t.get("max_depth", 6) if isinstance(t, dict) else 6
        )
        desc_str = f" — {desc}" if desc else ""
        lines.append(f"- **{name}**: `{from_sym}` -> `{to_sym}` (depth {depth}){desc_str}")
    return "\n".join(lines)


def format_doc_search_md(query: str, items: list[Any]) -> str:
    """Format documentation search chunks as Markdown with explicit scope tags."""
    if not items:
        return f"No documentation matches found for `{query}`."
    lines = [f"## Documentation Search Results for `{query}`", ""]
    for idx, it in enumerate(items, start=1):
        scope = getattr(it, "scope", "local")
        scope_upper = str(scope).upper()
        cat = getattr(it, "catalog", "")
        fpath = getattr(it, "file_path", "")
        hpath = getattr(it, "heading_path", "")
        s_line = getattr(it, "start_line", 1)
        e_line = getattr(it, "end_line", 1)
        score = getattr(it, "score", 0.0)
        content = getattr(it, "content", "").strip()
        lines.append(f"### {idx}. [{scope_upper}:{cat}] {fpath} (Lines {s_line}-{e_line})")
        meta = (
            f"**Tag:** `[{scope_upper}]` | **Catalog:** `{cat}` | "
            f"**Section:** `{hpath}` | **Score:** {score:.4f}"
        )
        lines.append(meta)
        lines.append("")
        lines.append(content)
        lines.append("")
    return "\n".join(lines).strip()


def format_doc_catalogs_md(catalogs: list[Any]) -> str:
    """Format catalog list as Markdown."""
    if not catalogs:
        return (
            "No documentation catalogs configured. Use `vibetrace docs catalog add <name> <path>`."
        )
    lines = ["## Documentation Catalogs", ""]
    for c in catalogs:
        name = getattr(c, "name", "")
        scope = getattr(c, "scope", "")
        d_cnt = getattr(c, "doc_count", 0)
        c_cnt = getattr(c, "chunk_count", 0)
        paths = getattr(c, "paths", [])
        lines.append(f"- **{name}** (`{scope}` scope) - {d_cnt} docs, {c_cnt} chunks")
        for p in paths:
            lines.append(f"  - `{p}`")
    return "\n".join(lines).strip()


def format_caller_guard_markdown(report: Any) -> str:
    """Format caller guard report for MCP responses."""
    from vibetrace.services.caller_guard import format_caller_guard_md

    return format_caller_guard_md(report)


def format_vibe_bundle_markdown(bundle: Any) -> str:
    """Format vibe bundle for MCP responses."""
    return getattr(bundle, "formatted_prompt", "")


def format_split_audit_md(report: Any) -> str:
    """Format architectural split audit report as clean Markdown."""
    lines = [
        "## Architectural Split Audit Report",
        "",
        f"- **Total Splits Detected:** {report.total_splits}",
        f"- **Approved:** {report.approved_count} ✅",
        f"- **Modified Code:** {report.modified_count} ⚠️",
        f"- **New Unapproved:** {report.new_unapproved_count} ❓",
        "",
    ]
    if not report.splits:
        lines.append("No cross-module bifurcations detected.")
        return "\n".join(lines)

    lines.append("| Status | Symbol | File | Branches | Hash | Rationale |")
    lines.append("| :--- | :--- | :--- | :--- | :--- | :--- |")

    for s in report.splits:
        st_icon = "❓ NEW"
        if s.status == "APPROVED":
            st_icon = "✅ APPROVED"
        elif s.status == "MODIFIED_CODE":
            st_icon = "⚠️ MODIFIED"
        elif s.status == "RISK":
            st_icon = "🛑 RISK"

        short_hash = s.code_hash[:8] if s.code_hash else "n/a"
        file_loc = f"`{s.file_path}:L{s.start_line}`"
        rationale = s.rationale or "-"
        lines.append(
            f"| {st_icon} | `{s.symbol}` | {file_loc} | "
            f"{s.branches_count} | `{short_hash}` | {rationale} |"
        )

    return "\n".join(lines).strip()
