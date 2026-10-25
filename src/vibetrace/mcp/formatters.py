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
) -> str:
    lines = [f"## Dependencies for `{norm_path}`"]
    if not (internal or third_party or stdlib):
        lines.append("No imports detected or file not indexed.")
        return "\n".join(lines)
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
    return "\n".join(lines)


def format_signature_search_md(pattern: str, rows: list[dict[str, Any]]) -> str:
    lines = [f"## Signature Search: `{pattern}`"]
    if not rows:
        lines.append("No matches found.")
    else:
        for row in rows:
            loc = f":L{row['start_line']}" if row.get("start_line") is not None else ""
            lines.append(
                f"- **`{row['name']}`** `{row.get('signature', '')}` in `{row['file_path']}{loc}`"
            )
    return "\n".join(lines)
