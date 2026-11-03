"""FastMCP tools for federated multi-repository project workspaces."""

from __future__ import annotations

import json
from pathlib import Path
from typing import TYPE_CHECKING

from vibetrace.federation.config import find_project_root
from vibetrace.federation.project import ProjectWorkspace
from vibetrace.mcp.schemas import (
    ProjectGuardInput,
    ProjectLinksInput,
    ProjectSearchInput,
    ProjectStatusInput,
    ResponseFormat,
)

if TYPE_CHECKING:
    from mcp.server.fastmcp import FastMCP


def _resolve_workspace(scope_path: str) -> ProjectWorkspace:
    target = Path(scope_path).resolve() if scope_path else Path.cwd().resolve()
    found = find_project_root(target)
    if not found:
        raise FileNotFoundError(
            f"No federated project workspace found in '{target}' or parent directories. "
            "Initialize with `vibetrace project init`."
        )
    return ProjectWorkspace.load(found)


def vibetrace_project_status(params: ProjectStatusInput) -> str:
    """Inspect health, index status, and node counts of all member repositories in a project."""
    try:
        ws = _resolve_workspace(params.scope_path)
        try:
            statuses = ws.list_members()
            if params.response_format == ResponseFormat.JSON:
                return json.dumps(
                    {
                        "manifest": ws.manifest.model_dump(),
                        "members": [s.model_dump() for s in statuses],
                    },
                    indent=2,
                )

            lines = [
                f"# Federated Project Workspace: `{ws.manifest.name}`",
                f"- **Storage Backend:** `{ws.manifest.settings.storage_backend}`",
                f"- **Member Repositories:** {len(statuses)}\n",
                "| Repository | Indexed | Nodes | Edges | Path |",
                "| :--- | :---: | :---: | :---: | :--- |",
            ]
            for s in statuses:
                stat_icon = "✓" if s.is_indexed else "✗"
                lines.append(
                    f"| `{s.name}` | {stat_icon} | {s.node_count} | {s.edge_count} | `{s.path}` |"
                )
            return "\n".join(lines)
        finally:
            ws.close()
    except Exception as e:
        return f"Error resolving project status: {e}"


def vibetrace_project_search(params: ProjectSearchInput) -> str:
    """Perform federated symbol and function search across one or all member repositories."""
    try:
        ws = _resolve_workspace(params.scope_path)
        try:
            results = ws.search(params.query, repo=params.repo, limit=params.limit)
            if params.response_format == ResponseFormat.JSON:
                return json.dumps([r.model_dump() for r in results], indent=2)

            scope_tag = f"in `[{params.repo}]`" if params.repo else "across all repositories"
            lines = [
                f"# Federated Search {scope_tag} for `{params.query}`",
                f"Found **{len(results)}** symbol definitions:\n",
            ]
            for r in results:
                sig = f" : `{r.signature}`" if r.signature else ""
                loc = f"`{r.file_path}:{r.start_line}`"
                lines.append(f"- `[{r.repo}]` **{r.name}** (`{r.kind}`) - {loc}{sig}")
            return "\n".join(lines)
        finally:
            ws.close()
    except Exception as e:
        return f"Error executing project search: {e}"


def vibetrace_project_guard(params: ProjectGuardInput) -> str:
    """Analyze cross-repository caller blast radius before editing a shared symbol or contract."""
    try:
        ws = _resolve_workspace(params.scope_path)
        try:
            report = ws.guard(params.symbol, target_repo=params.repo)
            if params.response_format == ResponseFormat.JSON:
                return json.dumps(report.model_dump(), indent=2)

            lines = [
                f"# Cross-Repository Blast Radius Guard: `{report.target_symbol}`",
                f"- **Defined in:** `[{report.target_repo}]` `{report.target_file}`",
                f"- **Total Callers:** {report.total_callers} | "
                f"**Risk Level:** `{report.risk_level}`",
                f"- **Safety Advisory:** {report.safety_advisory}\n",
            ]
            if report.callers_by_repo:
                lines.append("### Callers by Repository:")
                for r_name, callers in report.callers_by_repo.items():
                    lines.append(f"\n#### `[{r_name}]` ({len(callers)} caller(s)):")
                    for c in callers:
                        line_str = f":{c['call_line']}" if c.get("call_line") else ""
                        sym = c["caller_symbol"]
                        kind = c.get("caller_kind", "fn")
                        path_str = f"`{c['caller_file']}{line_str}`"
                        lines.append(f"- **{sym}** (`{kind}`) in {path_str}")
            return "\n".join(lines)
        finally:
            ws.close()
    except Exception as e:
        return f"Error running project caller guard: {e}"


def vibetrace_project_links(params: ProjectLinksInput) -> str:
    """Discover cross-repository HTTP route connections and RPC service contracts."""
    try:
        ws = _resolve_workspace(params.scope_path)
        try:
            links = ws.discover_cross_repo_links()
            if params.response_format == ResponseFormat.JSON:
                return json.dumps([lnk.model_dump() for lnk in links], indent=2)

            lines = [
                f"# Discovered Cross-Repository Contract Links ({len(links)})",
                "Links matching client-side API requests to backend route handlers:\n",
            ]
            if not links:
                lines.append("- *No cross-repository route contracts detected.*")
            for link in links:
                src = f"`[{link.source_repo}]` {link.source_symbol} (`{link.source_file}`)"
                tgt = f"`[{link.target_repo}]` {link.target_symbol} (`{link.target_file}`)"
                lines.append(f"- {src} ➔ **`{link.contract_key}`** ➔ {tgt}")
            return "\n".join(lines)
        finally:
            ws.close()
    except Exception as e:
        return f"Error discovering cross-repo links: {e}"


def register_project_tools(mcp: FastMCP) -> None:
    """Register federated project workspace tools onto the FastMCP server."""
    mcp.tool()(vibetrace_project_status)
    mcp.tool()(vibetrace_project_search)
    mcp.tool()(vibetrace_project_guard)
    mcp.tool()(vibetrace_project_links)
