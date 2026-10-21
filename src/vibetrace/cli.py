"""Unified VibeTrace CLI for AI Agents and Developers."""

from __future__ import annotations

import argparse
import sys

from vibetrace.cli_commands import (
    cmd_call_tree,
    cmd_hooks,
    cmd_impact,
    cmd_index,
    cmd_init,
    cmd_mcp,
    cmd_search,
    cmd_serve,
    cmd_skill,
    cmd_slice,
    cmd_summary,
    cmd_trace,
)

# Aliases for backward compatibility
_cmd_index = cmd_index
_cmd_search = cmd_search
_cmd_trace = cmd_trace
_cmd_slice = cmd_slice
_cmd_call_tree = cmd_call_tree
_cmd_impact = cmd_impact
_cmd_summary = cmd_summary
_cmd_hooks = cmd_hooks
_cmd_serve = cmd_serve
_cmd_init = cmd_init
_cmd_skill = cmd_skill
_cmd_mcp = cmd_mcp


def build_parser() -> argparse.ArgumentParser:
    epilog = """
examples:
  vibetrace index . --embed             # Full index with AST, FTS5, and 256d vectors
  vibetrace index . --lsp               # Index with dynamic LSP type inference
  vibetrace search "upsert_node"        # 4-stream hybrid search (AST + BM25 + Vector + PageRank)
  vibetrace trace "GET /graph" get_graph  # Bidirectional BFS trace from route to handler
  vibetrace trace "GET /graph" get_graph --save-as "GraphFlow"  # Save trace bookmark
  vibetrace trace --list-saved          # List saved trace queries
  vibetrace trace --saved "GraphFlow"   # Execute saved trace query
  vibetrace slice IndexerDB.upsert_node # Surgical source slice + callee contracts
  vibetrace call-tree hybrid_search     # Top-down recursive call tree hierarchy
  vibetrace impact src/vibetrace/db.py  # 3-level blast radius impact analysis
  vibetrace hooks status                # Inspect git hook background sync status
  vibetrace hooks install               # Auto-configure Lefthook or native git hooks
  vibetrace serve                       # Start Map Room API and WebSocket daemon
"""
    parser = argparse.ArgumentParser(
        prog="vibetrace",
        description="VibeTrace Code Intelligence CLI (AST + FTS5 BM25 + EmbeddingGemma 2)",
        epilog=epilog,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--root", default=None, help="Project root path override")
    parser.add_argument(
        "--no-sync",
        action="store_true",
        help="Disable Lazy JIT auto-sync (skip checking uncommitted file changes)",
    )
    parser.add_argument("--json", action="store_true", help="Emit JSON output for machine parsing")
    sub = parser.add_subparsers(dest="command", required=True)

    # index
    p_idx = sub.add_parser(
        "index",
        help="Index repository AST, FTS5, and optional embeddings",
        description=(
            "Extract code symbols, references, framework routes, and generate search indices."
        ),
    )
    p_idx.add_argument(
        "path",
        nargs="?",
        default=".",
        help="Directory or file path to index (default: current directory)",
    )
    p_idx.add_argument(
        "--embed", action="store_true", help="Compute EmbeddingGemma 2 256d Matryoshka vectors"
    )
    p_idx.add_argument(
        "--force",
        action="store_true",
        help="Force re-indexing all files (bypass Git-SHA delta cache)",
    )
    p_idx.add_argument(
        "--lsp",
        action="store_true",
        help="Enable LSP hybrid bridge for dynamic types and re-exports",
    )
    p_idx.add_argument("--json", action="store_true", default=False, help="Emit JSON output")
    p_idx.set_defaults(func=_cmd_index)

    # search
    p_search = sub.add_parser(
        "search",
        help="Hybrid RRF search across AST, FTS5, and vectors",
        description=(
            "Query codebase using 4-stream Reciprocal Rank Fusion with PageRank centrality boost."
        ),
    )
    p_search.add_argument("query", help="Search query symbol name or natural language intent")
    p_search.add_argument(
        "--limit", type=int, default=15, help="Maximum number of results to return (default: 15)"
    )
    p_search.add_argument("--path", default=".", help="Project root directory path")
    p_search.add_argument("--json", action="store_true", default=False, help="Emit JSON output")
    p_search.set_defaults(func=_cmd_search)

    # trace
    p_trace = sub.add_parser(
        "trace",
        help="Bidirectional BFS execution tracepath between symbols",
        description=(
            "Find execution paths between two symbols (functions, methods, HTTP routes) "
            "with call-site lines."
        ),
    )
    p_trace.add_argument(
        "from_symbol",
        nargs="?",
        default=None,
        help="Source symbol name or HTTP route (e.g. 'GET /graph', 'main')",
    )
    p_trace.add_argument(
        "to_symbol",
        nargs="?",
        default=None,
        help="Target symbol name (e.g. 'IndexerDB.upsert_node')",
    )
    p_trace.add_argument("--saved", default=None, help="Execute a named saved architectural trace")
    p_trace.add_argument(
        "--save-as", default=None, help="Bookmark this trace under a persistent name"
    )
    p_trace.add_argument(
        "--list-saved", action="store_true", help="List all saved trace queries in this project"
    )
    p_trace.add_argument(
        "--depth", type=int, default=6, help="Maximum BFS traversal depth in hops (default: 6)"
    )
    p_trace.add_argument(
        "--engine",
        choices=["bfs", "cte"],
        default="bfs",
        help="Path engine: 'bfs' (bidirectional BFS) or 'cte' (native SQLite CTE)",
    )
    p_trace.add_argument("--path", default=".", help="Project root directory path")
    p_trace.add_argument("--json", action="store_true", default=False, help="Emit JSON output")
    p_trace.set_defaults(func=_cmd_trace)

    # slice
    p_slice = sub.add_parser(
        "slice",
        help="Read exact symbol source lines and callee contracts",
        description=(
            "Surgically retrieve symbol source code without reading entire files, "
            "including direct outgoing callee contracts."
        ),
    )
    p_slice.add_argument("symbol", help="Target symbol name or ClassName.method_name")
    p_slice.add_argument(
        "--file",
        default=None,
        help="Optional relative file path to disambiguate identical symbol names",
    )
    p_slice.add_argument(
        "--no-callees",
        action="store_true",
        help="Omit outgoing callee contracts (source lines only)",
    )
    p_slice.add_argument("--path", default=".", help="Project root directory path")
    p_slice.add_argument("--json", action="store_true", default=False, help="Emit JSON output")
    p_slice.set_defaults(func=_cmd_slice)

    # call-tree
    p_tree = sub.add_parser(
        "call-tree",
        help="Explore top-down execution call tree from an entry point",
        description=(
            "Generate recursive ASCII call hierarchy with exact call-site line numbers "
            "and cycle prevention."
        ),
    )
    p_tree.add_argument(
        "symbol", help="Root entry point symbol name (e.g., 'main', 'hybrid_search')"
    )
    p_tree.add_argument(
        "--depth", type=int, default=3, help="Maximum depth of recursive nested calls (default: 3)"
    )
    p_tree.add_argument(
        "--file", default=None, help="Optional relative file path to disambiguate symbol"
    )
    p_tree.add_argument("--path", default=".", help="Project root directory path")
    p_tree.add_argument("--json", action="store_true", default=False, help="Emit JSON output")
    p_tree.set_defaults(func=_cmd_call_tree)

    # impact
    p_impact = sub.add_parser(
        "impact",
        help="Compute 3-level blast radius for a file",
        description=(
            "Compute upstream blast radius: Level 1 (direct callers), "
            "Level 2 (indirect), Level 3 (deep)."
        ),
    )
    p_impact.add_argument("file_path", help="Relative or absolute file path to analyze")
    p_impact.add_argument("--json", action="store_true", default=False, help="Emit JSON output")
    p_impact.set_defaults(func=_cmd_impact)

    # summary
    p_sum = sub.add_parser(
        "summary",
        help="Get structural outline of a file",
        description=(
            "Extract all functions, classes, methods, signatures, "
            "and visibility decorators in a file."
        ),
    )
    p_sum.add_argument("file_path", help="File path to summarize")
    p_sum.add_argument(
        "--limit", type=int, default=100, help="Maximum number of symbols to list (default: 100)"
    )
    p_sum.add_argument("--offset", type=int, default=0, help="Pagination offset (default: 0)")
    p_sum.add_argument("--json", action="store_true", default=False, help="Emit JSON output")
    p_sum.set_defaults(func=_cmd_summary)

    # serve
    p_serve = sub.add_parser(
        "serve",
        help="Start the VibeTrace API & Map Room WebSocket server",
        description=(
            "Launch FastAPI backend with bidirectional WebSocket support "
            "for interactive visualizers and agent tracking."
        ),
    )
    p_serve.add_argument("--host", default="127.0.0.1", help="Bind host (default: 127.0.0.1)")
    p_serve.add_argument("--port", type=int, default=8000, help="Bind port (default: 8000)")
    p_serve.set_defaults(func=_cmd_serve)

    # hooks
    p_hooks = sub.add_parser(
        "hooks",
        help="Manage automated post-commit and post-merge indexer hooks",
        description=(
            "Inspect, install, and uninstall post-commit and post-merge "
            "background index synchronization."
        ),
    )
    p_hooks.add_argument(
        "action",
        nargs="?",
        choices=["status", "install", "uninstall"],
        default="status",
        help=(
            "Hook action: 'status' (inspect configuration), "
            "'install' (configure hooks), 'uninstall' (remove hooks)"
        ),
    )
    p_hooks.add_argument(
        "--provider",
        choices=["auto", "lefthook", "git"],
        default="auto",
        help=(
            "Hook provider to configure: 'auto' (detect existing), "
            "'lefthook', or 'git' (default: auto)"
        ),
    )
    p_hooks.add_argument("--path", default=".", help="Project root directory path")
    p_hooks.add_argument("--json", action="store_true", default=False, help="Emit JSON output")
    p_hooks.set_defaults(func=_cmd_hooks)

    # init
    p_init = sub.add_parser(
        "init",
        help="Initialize VibeTrace in workspace (.gitignore, skill, isolated db)",
        description=(
            "Set up a project workspace with local .gitignore rules, install the "
            "vibetrace-navigator agent skill, and perform initial indexing."
        ),
    )
    p_init.add_argument(
        "path",
        nargs="?",
        default=".",
        help="Project workspace path to initialize (default: current directory)",
    )
    p_init.add_argument(
        "--no-index", action="store_true", help="Skip running initial index immediately"
    )
    p_init.add_argument(
        "--global",
        dest="global_skill",
        action="store_true",
        help="Also install skill into user global agent skills directory",
    )
    p_init.add_argument("--json", action="store_true", default=False, help="Emit JSON output")
    p_init.set_defaults(func=_cmd_init)

    # skill
    p_skill = sub.add_parser(
        "skill",
        help="Manage and install VibeTrace agent skills",
        description="Install the vibetrace-navigator skill for AI agents in workspace or globally.",
    )
    p_skill.add_argument(
        "action",
        nargs="?",
        choices=["install"],
        default="install",
        help="Skill action: 'install' (default: install)",
    )
    p_skill.add_argument(
        "--path", default=".", help="Project root directory path (default: current directory)"
    )
    p_skill.add_argument(
        "--global",
        dest="global_skill",
        action="store_true",
        help="Install into user global agent skills directory",
    )
    p_skill.add_argument("--json", action="store_true", default=False, help="Emit JSON output")
    p_skill.set_defaults(func=_cmd_skill)

    # mcp
    p_mcp = sub.add_parser(
        "mcp",
        help="Launch VibeTrace Model Context Protocol (MCP) stdio server",
        description="Run the MCP stdio transport server for Claude, Cursor, and other agents.",
    )
    p_mcp.add_argument(
        "--path", default=".", help="Project root directory path (default: current directory)"
    )
    p_mcp.set_defaults(func=_cmd_mcp)

    return parser


def main(argv: list[str] | None = None) -> int:
    if sys.platform == "win32":
        try:
            reconfig_out = getattr(sys.stdout, "reconfigure", None)
            if callable(reconfig_out):
                reconfig_out(encoding="utf-8")
            reconfig_err = getattr(sys.stderr, "reconfigure", None)
            if callable(reconfig_err):
                reconfig_err(encoding="utf-8")
        except Exception:
            pass

    top_json = False
    if argv is not None and "--json" in argv:
        top_json = True
    parser = build_parser()
    args = parser.parse_args(argv)
    if top_json:
        args.json = True
    if getattr(args, "root", None) and getattr(args, "path", ".") == ".":
        args.path = args.root
    code = int(args.func(args))
    if argv is None:
        sys.exit(code)
    return code


if __name__ == "__main__":
    main()
