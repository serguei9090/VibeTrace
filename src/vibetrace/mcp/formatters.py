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
            decs = json.loads(row["decorators"])
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


def format_semantic_search_md(query: str, results: list[SearchResult]) -> str:
    lines = [f"## Hybrid RRF Search: `{query}`"]
    if not results:
        lines.append("No matching symbols found.")
        return "\n".join(lines)

    for idx, hit in enumerate(results, start=1):
        sig = f" `{hit.signature}`" if hit.signature else ""
        span = f":L{hit.start_line}-{hit.end_line}" if hit.start_line else ""
        streams = ", ".join(hit.match_sources)
        cent_str = f" | cent={hit.centrality:.2f}" if hit.centrality > 0 else ""
        lines.append(
            f"{idx}. **`{hit.qualified_name}`** ({hit.kind}){sig} — "
            f"`{hit.file_path}{span}` `[RRF={hit.rrf_score:.4f} | {streams}{cent_str}]`"
        )
        if hit.docstring:
            first_line = hit.docstring.strip().splitlines()[0]
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
            lines.append(
                f"{hop.step_index}. **`{hop.from_node.qualified_name}`** "
                f"(`{hop.from_node.file_path}{call_ln}`) "
                f"{safe_str('→')} [{hop.relation_type}] "
                f"**`{hop.to_node.qualified_name}`** "
                f"(`{hop.to_node.file_path}:L{hop.to_node.start_line}`)"
            )
    return "\n".join(lines)


def format_code_slice_md(result: CodeSliceResult) -> str:
    if not result.found:
        return f"Symbol '{result.name}' not found in index."

    sig = f"`{result.signature}`" if result.signature else ""
    lines = [
        f"## Code Slice: `{result.qualified_name}` ({result.kind}) {sig}",
        f"**Location**: `{result.file_path}:L{result.start_line}-{result.end_line}`",
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
