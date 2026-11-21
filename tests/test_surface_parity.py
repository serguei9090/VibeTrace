"""Automated contract gate enforcing CLI <-> MCP <-> REST endpoint and argument parity."""

from __future__ import annotations

import argparse
import inspect
import typing
from dataclasses import dataclass

from fastapi.routing import APIRoute, APIWebSocketRoute

from vibetrace.cli import build_parser
from vibetrace.mcp.server import mcp
from vibetrace.server_api import app


@dataclass(frozen=True)
class CapabilitySpec:
    """Declares how a capability maps across CLI, MCP, and REST surfaces."""

    cli: tuple[str, ...]
    mcp: tuple[str, ...]
    rest: tuple[str, ...]


SHARED_CAPABILITIES: dict[str, CapabilitySpec] = {
    "index": CapabilitySpec(
        cli=("index",),
        mcp=("vibetrace_reindex_project",),
        rest=("POST /api/reindex",),
    ),
    "search": CapabilitySpec(
        cli=("search",),
        mcp=("vibetrace_semantic_search", "vibetrace_search_by_signature"),
        rest=("GET /api/search", "GET /api/search/signature"),
    ),
    "trace": CapabilitySpec(
        cli=("trace",),
        mcp=(
            "vibetrace_trace_path",
            "vibetrace_list_saved_traces",
            "vibetrace_save_trace",
            "vibetrace_delete_saved_trace",
        ),
        rest=(
            "GET /api/trace",
            "GET /api/saved-traces",
            "POST /api/saved-traces",
            "DELETE /api/saved-traces/by-name/{name}",
        ),
    ),
    "slice": CapabilitySpec(
        cli=("slice",),
        mcp=("vibetrace_get_code_slice",),
        rest=("GET /api/slice",),
    ),
    "call_tree": CapabilitySpec(
        cli=("call-tree",),
        mcp=("vibetrace_get_call_tree",),
        rest=("GET /api/call-tree",),
    ),
    "call_stack": CapabilitySpec(
        cli=("call-stack",),
        mcp=("vibetrace_get_call_stack",),
        rest=("GET /api/call-stack",),
    ),
    "impact": CapabilitySpec(
        cli=("impact",),
        mcp=("vibetrace_impact_analysis",),
        rest=("GET /api/impact",),
    ),
    "summary": CapabilitySpec(
        cli=("summary",),
        mcp=("vibetrace_get_structural_summary",),
        rest=("GET /api/summary",),
    ),
    "references": CapabilitySpec(
        cli=("references",),
        mcp=("vibetrace_find_references",),
        rest=("GET /api/references",),
    ),
    "dependencies": CapabilitySpec(
        cli=("dependencies",),
        mcp=("vibetrace_get_dependencies",),
        rest=("GET /api/dependencies",),
    ),
    "transitive_deps": CapabilitySpec(
        cli=("deps why", "deps tree", "deps conflicts"),
        mcp=("vibetrace_explain_dependency",),
        rest=("GET /api/deps/why", "GET /api/deps/tree", "GET /api/deps/conflicts"),
    ),
    "docs_catalogs": CapabilitySpec(
        cli=("docs catalog list",),
        mcp=("vibetrace_list_doc_catalogs",),
        rest=("GET /api/docs/catalogs",),
    ),
    "docs_search": CapabilitySpec(
        cli=("docs search",),
        mcp=("vibetrace_search_docs",),
        rest=("GET /api/docs/search",),
    ),
    "docs_check": CapabilitySpec(
        cli=("docs check",),
        mcp=("vibetrace_check_docs_drift",),
        rest=("GET /api/docs/check",),
    ),
    "guard": CapabilitySpec(
        cli=("guard",),
        mcp=("vibetrace_check_caller_guard",),
        rest=("GET /api/vibe/guard",),
    ),
    "bundle": CapabilitySpec(
        cli=("bundle",),
        mcp=("vibetrace_get_vibe_bundle",),
        rest=("GET /api/vibe/bundle",),
    ),
    "refactor": CapabilitySpec(
        cli=("refactor",),
        mcp=("vibetrace_refactor_method",),
        rest=("GET /api/refactor/candidates", "POST /api/refactor/split"),
    ),
    "audit_splits": CapabilitySpec(
        cli=("audit splits",),
        mcp=("vibetrace_audit_splits", "vibetrace_approve_split"),
        rest=("GET /api/splits/audit", "POST /api/splits/approve"),
    ),
    "audit_security": CapabilitySpec(
        cli=("audit security",),
        mcp=(
            "vibetrace_audit_security",
            "vibetrace_approve_security_exception",
            "vibetrace_remediate_vulnerability",
        ),
        rest=(
            "GET /api/security/audit",
            "POST /api/security/approve",
            "POST /api/security/remediate",
        ),
    ),
    "audit_quality": CapabilitySpec(
        cli=("audit quality",),
        mcp=("vibetrace_audit_quality", "vibetrace_approve_quality_exception"),
        rest=("GET /api/quality/audit", "POST /api/quality/approve"),
    ),
    "fix_deps": CapabilitySpec(
        cli=("audit fix-deps",),
        mcp=("vibetrace_patch_dependency",),
        rest=("POST /api/security/patch-deps",),
    ),
    "pr_taint": CapabilitySpec(
        cli=("audit pr-taint",),
        mcp=("vibetrace_audit_pr_taint",),
        rest=("GET /api/security/pr-taint",),
    ),
    "review": CapabilitySpec(
        cli=("review",),
        mcp=("vibetrace_review_changes",),
        rest=("GET /api/review",),
    ),
    "diagnose": CapabilitySpec(
        cli=("diagnose",),
        mcp=("vibetrace_diagnose_symbol",),
        rest=("GET /api/diagnose",),
    ),
    "dead_code": CapabilitySpec(
        cli=("dead-code",),
        mcp=("vibetrace_dead_code",),
        rest=("GET /api/dead-code",),
    ),
    "project_status": CapabilitySpec(
        cli=("project list",),
        mcp=("vibetrace_project_status",),
        rest=("GET /api/project",),
    ),
    "project_search": CapabilitySpec(
        cli=("project search",),
        mcp=("vibetrace_project_search",),
        rest=("GET /api/project/search",),
    ),
    "project_guard": CapabilitySpec(
        cli=("project guard",),
        mcp=("vibetrace_project_guard",),
        rest=("GET /api/project/guard",),
    ),
    "project_links": CapabilitySpec(
        cli=("project links",),
        mcp=("vibetrace_project_links",),
        rest=("GET /api/project/links",),
    ),
}

CLI_ONLY_COMMANDS: set[str] = {
    "config quality",
    "docs catalog add",
    "docs catalog remove",
    "docs index",
    "docs sync-links",
    "hooks",
    "init",
    "mcp",
    "project add",
    "project index",
    "project init",
    "project remove",
    "serve",
    "skill",
    "test",
    "tools",
}

REST_ONLY_ROUTES: set[str] = {
    # Legacy unprefixed aliases
    "GET /call-stack",
    "GET /call-tree",
    "GET /graph",
    "GET /source",
    "GET /trace",
    # Map Room Web UI & WebSocket routes
    "GET /api/graph",
    "GET /api/node/{node_id}/neighbors",
    "GET /api/reachable",
    "GET /api/source",
    "POST /api/agent-focus",
    "WS /ws",
    # REST-only management & saved-trace helper endpoints
    "DELETE /api/docs/catalogs/{name}",
    "POST /api/docs/catalogs",
    "POST /api/docs/index",
    "POST /api/docs/sync-links",
    "GET /api/hooks",
    "POST /api/hooks",
    "DELETE /api/saved-traces/{trace_id}",
    "GET /api/saved-traces/{name}/run",
    "GET /api/saved-traces/{trace_id}",
    "GET /api/skill",
    "POST /api/skill",
}

MCP_ONLY_TOOLS: set[str] = set()


def _collect_cli_leaf_commands() -> set[str]:
    leaves: set[str] = set()

    def _walk(parser: argparse.ArgumentParser, prefix: str) -> None:
        sub_action = next(
            (a for a in parser._actions if isinstance(a, argparse._SubParsersAction)),
            None,
        )
        if sub_action is not None:
            for name, subparser in sub_action.choices.items():
                next_prefix = f"{prefix} {name}".strip()
                _walk(subparser, next_prefix)
        elif prefix:
            leaves.add(prefix)

    _walk(build_parser(), "")
    return leaves


def _collect_mcp_tools() -> dict[str, set[str]]:
    tools_map: dict[str, set[str]] = {}
    for name, tool in mcp._tool_manager._tools.items():
        hints = typing.get_type_hints(tool.fn)
        params_model = hints.get("params")
        fields = (
            set(params_model.model_fields.keys())
            if params_model is not None and hasattr(params_model, "model_fields")
            else set()
        )
        tools_map[name] = fields
    return tools_map


def _collect_rest_routes() -> dict[str, set[str]]:
    routes_map: dict[str, set[str]] = {}
    for route in app.routes:
        if isinstance(route, APIRoute):
            param_names = set(inspect.signature(route.endpoint).parameters.keys())
            for method in sorted(route.methods - {"HEAD", "OPTIONS"}):
                routes_map[f"{method} {route.path}"] = param_names
        elif isinstance(route, APIWebSocketRoute):
            param_names = {
                k for k in inspect.signature(route.endpoint).parameters if k != "websocket"
            }
            routes_map[f"WS {route.path}"] = param_names
    return routes_map


def test_all_shared_capabilities_exist_across_declared_surfaces() -> None:
    cli_cmds = _collect_cli_leaf_commands()
    mcp_tools = set(_collect_mcp_tools())
    rest_routes = set(_collect_rest_routes())

    for cap_name, spec in SHARED_CAPABILITIES.items():
        for cmd in spec.cli:
            assert cmd in cli_cmds, f"[{cap_name}] missing CLI command: {cmd}"
        for tool in spec.mcp:
            assert tool in mcp_tools, f"[{cap_name}] missing MCP tool: {tool}"
        for route in spec.rest:
            assert route in rest_routes, f"[{cap_name}] missing REST route: {route}"


def test_no_untracked_cli_mcp_or_rest_endpoints() -> None:
    cli_cmds = _collect_cli_leaf_commands()
    mcp_tools = set(_collect_mcp_tools())
    rest_routes = set(_collect_rest_routes())

    mapped_cli = {cmd for spec in SHARED_CAPABILITIES.values() for cmd in spec.cli}
    mapped_mcp = {tool for spec in SHARED_CAPABILITIES.values() for tool in spec.mcp}
    mapped_rest = {route for spec in SHARED_CAPABILITIES.values() for route in spec.rest}

    untracked_cli = sorted(cli_cmds - mapped_cli - CLI_ONLY_COMMANDS)
    untracked_mcp = sorted(mcp_tools - mapped_mcp - MCP_ONLY_TOOLS)
    untracked_rest = sorted(rest_routes - mapped_rest - REST_ONLY_ROUTES)

    assert not untracked_cli, (
        f"Untracked CLI commands (add to SHARED_CAPABILITIES or CLI_ONLY_COMMANDS): {untracked_cli}"
    )
    assert not untracked_mcp, (
        f"Untracked MCP tools (add to SHARED_CAPABILITIES or MCP_ONLY_TOOLS): {untracked_mcp}"
    )
    assert not untracked_rest, (
        f"Untracked REST routes (add to SHARED_CAPABILITIES or REST_ONLY_ROUTES): {untracked_rest}"
    )


def test_shared_endpoint_argument_parity() -> None:
    mcp_tools = _collect_mcp_tools()
    rest_routes = _collect_rest_routes()

    sec_shared = {
        "file_path",
        "skip_deps",
        "update",
        "no_taint",
        "scan_all",
        "include_tests",
        "skip_trivy",
        "skip_semgrep",
        "enable_sonar",
        "image_tag",
    }
    assert sec_shared <= mcp_tools["vibetrace_audit_security"]
    assert sec_shared <= rest_routes["GET /api/security/audit"]

    qual_shared = {
        "file_path",
        "threshold",
        "include_tests",
        "typecheck",
        "enable_sonar",
        "scan_all",
    }
    assert qual_shared <= mcp_tools["vibetrace_audit_quality"]
    assert qual_shared <= rest_routes["GET /api/quality/audit"]

    dead_shared = {"limit", "include_tests", "exclude_exported"}
    assert dead_shared <= mcp_tools["vibetrace_dead_code"]
    assert dead_shared <= rest_routes["GET /api/dead-code"]

    docs_shared = {"catalog", "scope", "limit", "locale"}
    assert docs_shared <= mcp_tools["vibetrace_search_docs"]
    assert docs_shared <= rest_routes["GET /api/docs/search"]

    pr_shared = {"base_ref", "staged", "pr_number", "apply"}
    assert pr_shared <= mcp_tools["vibetrace_audit_pr_taint"]
    assert pr_shared <= rest_routes["GET /api/security/pr-taint"]

    assert {"device", "force", "use_lsp", "embed"} <= mcp_tools["vibetrace_reindex_project"]
