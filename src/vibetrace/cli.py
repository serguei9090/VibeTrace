"""Unified VibeTrace CLI for AI Agents and Developers."""

from __future__ import annotations

import argparse
import sys

from vibetrace.cli_commands import (
    cmd_call_stack,
    cmd_call_tree,
    cmd_dependencies,
    cmd_docs,
    cmd_hooks,
    cmd_impact,
    cmd_index,
    cmd_init,
    cmd_mcp,
    cmd_references,
    cmd_search,
    cmd_serve,
    cmd_skill,
    cmd_slice,
    cmd_summary,
    cmd_trace,
)
from vibetrace.cli_vibe import cmd_bundle, cmd_guard

# Aliases for backward compatibility
_cmd_index = cmd_index
_cmd_search = cmd_search
_cmd_trace = cmd_trace
_cmd_slice = cmd_slice
_cmd_call_stack = cmd_call_stack
_cmd_call_tree = cmd_call_tree
_cmd_impact = cmd_impact
_cmd_summary = cmd_summary
_cmd_references = cmd_references
_cmd_dependencies = cmd_dependencies
_cmd_hooks = cmd_hooks
_cmd_serve = cmd_serve
_cmd_init = cmd_init
_cmd_skill = cmd_skill
_cmd_mcp = cmd_mcp
_cmd_docs = cmd_docs
_cmd_guard = cmd_guard
_cmd_bundle = cmd_bundle


def build_parser() -> argparse.ArgumentParser:
    epilog = """
examples:
  vibetrace index . --embed             # Full index with AST, FTS5, and 256d vectors
  vibetrace index . --lsp               # Index with dynamic LSP type inference
  vibetrace guard upsert_node           # Pre-flight caller check before editing code
  vibetrace bundle "add auth token"     # One-shot context packing (docs+code+guard+tests)
  vibetrace search "upsert_node"        # 4-stream hybrid search (AST + BM25 + Vector + PageRank)
  vibetrace trace "GET /graph" get_graph  # Bidirectional BFS trace from route to handler
  vibetrace trace "GET /graph" get_graph --save-as "GraphFlow"  # Save trace bookmark
  vibetrace trace --list-saved          # List saved trace queries
  vibetrace trace --saved "GraphFlow"   # Execute saved trace query
  vibetrace slice IndexerDB.upsert_node # Surgical source slice + callee contracts
  vibetrace call-stack upsert_node      # Bidirectional call stack exploration
  vibetrace call-tree hybrid_search     # Top-down recursive call tree hierarchy
  vibetrace impact src/vibetrace/db.py  # 3-level blast radius impact analysis
  vibetrace references upsert_node     # Find incoming callers and references
  vibetrace dependencies src/db.py     # Categorize file imports (internal/stdlib/third-party)
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
    p_idx.add_argument(
        "--no-docs",
        action="store_true",
        default=False,
        help="Skip synchronizing local documentation catalogs",
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
    p_search.add_argument(
        "--offset",
        type=int,
        default=0,
        help="Pagination offset for signature search (default: 0)",
    )
    p_search.add_argument(
        "--sig",
        "--signature",
        dest="signature",
        action="store_true",
        default=False,
        help="Search function signatures by SQL pattern or substring",
    )
    p_search.add_argument(
        "--with-docs",
        "--docs",
        dest="with_docs",
        action="store_true",
        default=False,
        help="Include relevant documentation chunks alongside code symbols",
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
        "--delete-saved", default=None, help="Delete a saved architectural trace by name"
    )
    p_trace.add_argument(
        "--depth", type=int, default=None, help="Maximum BFS traversal depth in hops (default: 6)"
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

    # call-stack
    p_stack = sub.add_parser(
        "call-stack",
        help="Explore incoming/outgoing call stack hierarchy for a symbol",
        description="Trace callers (incoming), callees (outgoing), or both up to N depth levels.",
    )
    p_stack.add_argument("symbol", help="Target symbol or function name")
    p_stack.add_argument(
        "--direction",
        choices=["up", "down", "both"],
        default="both",
        help="Traversal direction: up (callers), down (callees), or both (default: both)",
    )
    p_stack.add_argument(
        "--depth", type=int, default=3, help="Maximum traversal depth (default: 3)"
    )
    p_stack.add_argument(
        "--file", default=None, help="Optional relative file path to disambiguate symbol"
    )
    p_stack.add_argument(
        "--all-relations",
        action="store_true",
        default=False,
        help="Include non-executable AST relations (defines, imports, references)",
    )
    p_stack.add_argument("--path", default=".", help="Project root directory path")
    p_stack.add_argument("--json", action="store_true", default=False, help="Emit JSON output")
    p_stack.set_defaults(func=_cmd_call_stack)

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

    # references
    p_refs = sub.add_parser(
        "references",
        help="Find incoming callers and references to a symbol",
        description=(
            "Search call sites, inheritance, and usages pointing to a function, method, or class."
        ),
    )
    p_refs.add_argument("symbol", help="Target symbol name to find references for")
    p_refs.add_argument("--file", default=None, help="Optional file path to disambiguate symbol")
    p_refs.add_argument(
        "--kind",
        default=None,
        choices=["calls", "references", "inherits", "implements", "routes"],
        help="Filter by relation type",
    )
    p_refs.add_argument("--path", default=".", help="Project root directory path")
    p_refs.add_argument("--json", action="store_true", default=False, help="Emit JSON output")
    p_refs.set_defaults(func=_cmd_references)

    # dependencies
    p_deps = sub.add_parser(
        "dependencies",
        help="Categorize imported modules in a file",
        description=(
            "Inspect internal project modules, third-party packages, "
            "and standard library dependencies."
        ),
    )
    p_deps.add_argument("file_path", help="Relative or absolute file path to analyze")
    p_deps.add_argument(
        "--reverse",
        "--dependents",
        dest="reverse",
        action="store_true",
        default=False,
        help="Show only inbound dependents (files that import this file)",
    )
    p_deps.add_argument("--path", default=".", help="Project root directory path")
    p_deps.add_argument("--json", action="store_true", default=False, help="Emit JSON output")
    p_deps.set_defaults(func=_cmd_dependencies)

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
    p_serve.add_argument(
        "--path", default=".", help="Project workspace path (default: current directory)"
    )
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
    p_init.add_argument(
        "--hooks",
        action="store_true",
        default=False,
        help="Automatically install Git/Lefthook pre-commit guard hooks",
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

    # docs
    p_docs = sub.add_parser(
        "docs",
        help="Manage and search documentation catalogs (.md, .txt, .pdf)",
        description="Index and search multi-catalog documentation with hybrid RRF.",
    )
    docs_sub = p_docs.add_subparsers(dest="docs_action", required=True)

    # docs catalog
    p_cat = docs_sub.add_parser("catalog", help="Manage documentation catalogs")
    cat_sub = p_cat.add_subparsers(dest="catalog_action", required=True)

    p_cat_add = cat_sub.add_parser("add", help="Add path to a documentation catalog")
    p_cat_add.add_argument("name", help="Catalog name (e.g. 'default', 'specs', 'master_stack')")
    p_cat_add.add_argument("path", help="Folder or file path to add")
    p_cat_add.add_argument(
        "--global",
        dest="global_scope",
        action="store_true",
        help="Add to global config (~/.vibetrace/config.toml)",
    )

    cat_sub.add_parser("list", help="List all documentation catalogs")

    p_cat_rm = cat_sub.add_parser("remove", help="Remove a documentation catalog")
    p_cat_rm.add_argument("name", help="Catalog name to remove")
    p_cat_rm.add_argument(
        "--global",
        dest="global_scope",
        action="store_true",
        help="Remove from global config",
    )

    # docs index
    p_doc_idx = docs_sub.add_parser("index", help="Index documentation catalogs")
    p_doc_idx.add_argument(
        "catalog",
        nargs="?",
        default=None,
        help="Specific catalog name to index (default: all)",
    )
    p_doc_idx.add_argument(
        "--global",
        dest="global_scope",
        action="store_true",
        help="Index global catalogs only",
    )
    p_doc_idx.add_argument(
        "--force",
        action="store_true",
        help="Force re-indexing without mtime check",
    )

    # docs search
    p_doc_search = docs_sub.add_parser(
        "search", help="Search documentation catalogs with hybrid RRF"
    )
    p_doc_search.add_argument("query", help="Search query string")
    p_doc_search.add_argument(
        "--catalog",
        default=None,
        help="Scope search to specific catalog",
    )
    p_doc_search.add_argument(
        "--scope",
        choices=["local", "global", "all"],
        default="local",
        help="Catalog search scope (default: local)",
    )
    p_doc_search.add_argument(
        "--limit",
        type=int,
        default=5,
        help="Maximum number of chunks to return",
    )

    p_docs.set_defaults(func=_cmd_docs)

    # guard
    p_guard = sub.add_parser(
        "guard",
        help="Pre-flight caller inspection to verify blast radius before editing code",
        description=(
            "Find all incoming callers, signatures, and call sites of a symbol "
            "to prevent breaking callers."
        ),
    )
    p_guard.add_argument(
        "symbol",
        nargs="?",
        default="",
        help="Target symbol name to inspect before editing",
    )
    p_guard.add_argument(
        "--staged",
        action="store_true",
        help="Inspect all symbol definitions modified in staged git diff",
    )
    p_guard.add_argument(
        "--strict",
        action="store_true",
        help="Exit with code 1 if any staged symbol modification affects callers",
    )
    p_guard.add_argument("--file", default=None, help="File path defining the target symbol")
    p_guard.add_argument(
        "--path", default=".", help="Project root directory path (default: current directory)"
    )
    p_guard.set_defaults(func=_cmd_guard)

    # bundle
    p_bundle = sub.add_parser(
        "bundle",
        help="Assemble one-shot task context packed with docs, code slice, caller guard, and tests",
        description=(
            "One-shot token-bounded task context bundle for AI pair programming (<= 1,500 tokens)."
        ),
    )
    p_bundle.add_argument("intent", help="Natural language task intent or query")
    p_bundle.add_argument("--symbol", default=None, help="Explicit target symbol override")
    p_bundle.add_argument(
        "--budget",
        type=int,
        default=1500,
        help="Maximum token budget for context bundle (default: 1500)",
    )
    p_bundle.add_argument(
        "--path", default=".", help="Project root directory path (default: current directory)"
    )
    p_bundle.set_defaults(func=_cmd_bundle)

    # Accept global flags after the subcommand too; SUPPRESS keeps root-level values intact.
    def _add_common_flags(parser_obj: argparse.ArgumentParser) -> None:
        if not any("--root" in a.option_strings for a in parser_obj._actions):
            parser_obj.add_argument(
                "--root", default=argparse.SUPPRESS, help="Project root path override"
            )
        if not any("--no-sync" in a.option_strings for a in parser_obj._actions):
            parser_obj.add_argument("--no-sync", action="store_true", default=argparse.SUPPRESS)
        if not any("--json" in a.option_strings for a in parser_obj._actions):
            parser_obj.add_argument("--json", action="store_true", default=argparse.SUPPRESS)

        for act in parser_obj._actions:
            if isinstance(act, argparse._SubParsersAction):
                for child_p in act.choices.values():
                    _add_common_flags(child_p)

    for sp in sub.choices.values():
        _add_common_flags(sp)

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

    eff_argv = sys.argv[1:] if argv is None else argv
    top_json = "--json" in eff_argv
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
