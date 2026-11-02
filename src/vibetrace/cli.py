"""Unified VibeTrace CLI for AI Agents and Developers."""

from __future__ import annotations

import argparse
import sys

from vibetrace.cli_audit import (
    cmd_audit_fix_deps,
    cmd_audit_pr_taint,
    cmd_audit_quality,
    cmd_audit_security,
    cmd_audit_splits,
    cmd_review,
)
from vibetrace.cli_commands import (
    cmd_call_stack,
    cmd_call_tree,
    cmd_dependencies,
    cmd_diagnose,
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
    cmd_test,
    cmd_trace,
)
from vibetrace.cli_config import register_config_parser
from vibetrace.cli_deps import cmd_deps
from vibetrace.cli_project import register_project_parser
from vibetrace.cli_refactor import cmd_refactor
from vibetrace.cli_tools import cmd_tools
from vibetrace.cli_vibe import cmd_bundle, cmd_guard


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
  vibetrace audit splits                # Audit cross-module splits & interactive triage
  vibetrace audit splits --ci           # CI gate: fail on unapproved or modified splits
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
    p_idx.add_argument(
        "--scip",
        type=str,
        default=None,
        help="Import external SCIP index file (.json or .scip dump)",
    )
    p_idx.add_argument("--json", action="store_true", default=False, help="Emit JSON output")
    p_idx.set_defaults(func=cmd_index)

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
    p_search.set_defaults(func=cmd_search)

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
    p_trace.set_defaults(func=cmd_trace)

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
    p_slice.add_argument(
        "--no-docs",
        action="store_true",
        help="Omit linked documentation chunks",
    )
    p_slice.add_argument(
        "--max-doc-tokens",
        type=int,
        default=300,
        help="Maximum total token budget for linked documentation specs (default: 300)",
    )
    p_slice.add_argument("--path", default=".", help="Project root directory path")
    p_slice.add_argument("--json", action="store_true", default=False, help="Emit JSON output")
    p_slice.set_defaults(func=cmd_slice)

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
    p_tree.set_defaults(func=cmd_call_tree)

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
    p_stack.set_defaults(func=cmd_call_stack)

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
    p_impact.set_defaults(func=cmd_impact)

    # summary
    p_sum = sub.add_parser(
        "summary",
        help="Get structural outline of a file or a token-budgeted repo map",
        description=(
            "Extract all functions, classes, methods, signatures, "
            "or generate a PageRank centrality repo map within a token budget."
        ),
    )
    p_sum.add_argument(
        "file_path",
        nargs="?",
        default=".",
        help="File or directory path to summarize (default: . for full repo map)",
    )
    p_sum.add_argument(
        "--budget",
        type=int,
        default=None,
        help="Maximum token budget for PageRank centrality-ranked repo map (e.g. 512, 1024, 2048)",
    )
    p_sum.add_argument(
        "--limit", type=int, default=100, help="Maximum number of symbols to list (default: 100)"
    )
    p_sum.add_argument("--offset", type=int, default=0, help="Pagination offset (default: 0)")
    p_sum.add_argument("--json", action="store_true", default=False, help="Emit JSON output")
    p_sum.set_defaults(func=cmd_summary)

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
    p_refs.set_defaults(func=cmd_references)

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
    p_deps.set_defaults(func=cmd_dependencies)

    # deps (transitive dependency resolution & root-cause visualization)
    p_transitive_deps = sub.add_parser(
        "deps",
        help="Resolve transitive package dependencies, causation chains, and diamond conflicts",
        description=(
            "Inspect project package graphs across lockfiles (uv.lock, package-lock.json, "
            "Cargo.lock) to pinpoint why transitive packages are installed and resolve conflicts."
        ),
    )
    deps_sub = p_transitive_deps.add_subparsers(dest="deps_action", required=True)

    # deps why
    p_why = deps_sub.add_parser(
        "why",
        help="Explain why a transitive package is installed by finding all root causation paths",
    )
    p_why.add_argument("package", help="Target package name (e.g. 'certifi', 'idna', 'qs')")
    p_why.add_argument("--path", default=".", help="Project root directory path (default: .)")
    p_why.add_argument("--json", action="store_true", default=False, help="Emit JSON output")

    # deps tree
    p_tree = deps_sub.add_parser(
        "tree",
        help="Render top-down package dependency tree from root manifest dependencies",
    )
    p_tree.add_argument(
        "--target",
        default=None,
        help="Filter tree to causation paths reaching this target package",
    )
    p_tree.add_argument(
        "--depth",
        type=int,
        default=4,
        help="Maximum depth of nested package dependencies (default: 4)",
    )
    p_tree.add_argument("--path", default=".", help="Project root directory path (default: .)")
    p_tree.add_argument("--json", action="store_true", default=False, help="Emit JSON output")

    # deps conflicts
    p_conflicts = deps_sub.add_parser(
        "conflicts",
        help="Audit lockfiles for diamond dependency version collisions",
    )
    p_conflicts.add_argument("--path", default=".", help="Project root directory path (default: .)")
    p_conflicts.add_argument("--json", action="store_true", default=False, help="Emit JSON output")

    p_transitive_deps.set_defaults(func=cmd_deps)

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
    p_serve.set_defaults(func=cmd_serve)

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
    p_hooks.set_defaults(func=cmd_hooks)

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
    p_init.add_argument(
        "--project",
        action="store_true",
        default=False,
        help="Initialize as a federated multi-repository / monorepo workspace",
    )
    p_init.add_argument("--json", action="store_true", default=False, help="Emit JSON output")
    p_init.set_defaults(func=cmd_init)

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
    p_skill.set_defaults(func=cmd_skill)

    # mcp
    p_mcp = sub.add_parser(
        "mcp",
        help="Launch VibeTrace Model Context Protocol (MCP) stdio server",
        description="Run the MCP stdio transport server for Claude, Cursor, and other agents.",
    )
    p_mcp.add_argument(
        "--path", default=".", help="Project root directory path (default: current directory)"
    )
    p_mcp.set_defaults(func=cmd_mcp)

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

    # docs check
    p_doc_check = docs_sub.add_parser(
        "check",
        help="Audit documentation links against code symbols for dead links and stale names",
    )
    p_doc_check.add_argument(
        "--path",
        default=".",
        help="Project root directory path (default: current directory)",
    )
    p_doc_check.add_argument(
        "--json",
        action="store_true",
        default=False,
        help="Emit JSON output",
    )

    # docs sync-links
    p_doc_sync = docs_sub.add_parser(
        "sync-links",
        help="Scan indexed Markdown documentation chunks and sync symbol links with codebase",
    )
    p_doc_sync.add_argument(
        "--path",
        default=".",
        help="Project root directory path (default: current directory)",
    )
    p_doc_sync.add_argument(
        "--json",
        action="store_true",
        default=False,
        help="Emit JSON output",
    )

    p_docs.set_defaults(func=cmd_docs)

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
    p_guard.set_defaults(func=cmd_guard)

    # bundle
    p_bundle = sub.add_parser(
        "bundle",
        help="Assemble one-shot task context packed with docs, code slice, caller guard, and tests",
        description=(
            "One-shot token-bounded task context bundle for AI pair programming (<= 1,500 tokens)."
        ),
    )
    p_bundle.add_argument(
        "intent", nargs="?", default="", help="Natural language task intent or query"
    )
    p_bundle.add_argument("--symbol", default=None, help="Explicit target symbol override")
    p_bundle.add_argument(
        "--session",
        default=None,
        help="Session identifier for multi-turn adaptive sliding window tracking",
    )
    p_bundle.add_argument(
        "--focus",
        default=None,
        help="Shift active focus symbol down the call-stack in an active session",
    )
    p_bundle.add_argument(
        "--reset-session",
        action="store_true",
        help="Reset and clear an existing multi-turn adaptive session",
    )
    p_bundle.add_argument(
        "--budget",
        type=int,
        default=1500,
        help="Maximum token budget for context bundle (default: 1500)",
    )
    p_bundle.add_argument(
        "--path", default=".", help="Project root directory path (default: current directory)"
    )
    p_bundle.set_defaults(func=cmd_bundle)

    # test (predictive test selection)
    p_test = sub.add_parser(
        "test",
        help="Execute only tests impacted by modified symbols",
        description=(
            "Select and execute tests impacted by git-changed symbols or specified targets."
        ),
    )
    p_test.add_argument(
        "--impacted",
        action="store_true",
        default=True,
        help="Find and run tests impacted by uncommitted or staged changes (default: True)",
    )
    p_test.add_argument(
        "--staged",
        action="store_true",
        help="Only analyze git staged changes instead of all working changes",
    )
    p_test.add_argument(
        "--symbol",
        default=None,
        help="Target symbol name to find impacted tests for",
    )
    p_test.add_argument(
        "--dry-run",
        action="store_true",
        help="List impacted tests and generated command without executing",
    )
    p_test.add_argument(
        "--runner",
        default=None,
        choices=["pytest", "bun", "go", "cargo"],
        help="Override test runner executable",
    )
    p_test.add_argument(
        "--path", default=".", help="Project root directory path (default: current directory)"
    )
    p_test.set_defaults(func=cmd_test)

    # refactor (interactive AST auto-refactoring)
    p_refactor = sub.add_parser(
        "refactor",
        help="Interactive AST auto-refactoring and method extraction",
        description=(
            "Extract complex statement blocks into decoupled helper functions "
            "with mathematical variable lifetime guarantees."
        ),
    )
    p_refactor.add_argument("file", help="Path to source file containing the target function")
    p_refactor.add_argument("function", help="Target function name to refactor")
    p_refactor.add_argument(
        "--suggest",
        action="store_true",
        help="Suggest candidate statement blocks ranked by complexity reduction",
    )
    p_refactor.add_argument(
        "--split-method",
        action="store_true",
        default=True,
        help="Perform split-method extraction (default: True)",
    )
    p_refactor.add_argument(
        "--start-line",
        type=int,
        default=None,
        help="Start line of statement block to extract",
    )
    p_refactor.add_argument(
        "--end-line",
        type=int,
        default=None,
        help="End line of statement block to extract",
    )
    p_refactor.add_argument(
        "--helper-name",
        type=str,
        default=None,
        help="Name of extracted helper function (default: _<func>_helper)",
    )
    p_refactor.add_argument(
        "--apply",
        action="store_true",
        default=False,
        help="Apply refactoring changes in-place to disk",
    )
    p_refactor.add_argument(
        "--dry-run",
        action="store_true",
        default=True,
        help="Preview diff and generated code without writing to disk (default: True)",
    )
    p_refactor.add_argument(
        "--path", default=".", help="Project root directory path (default: current directory)"
    )
    p_refactor.set_defaults(func=cmd_refactor)

    # audit (architectural pattern & split triage)
    p_audit = sub.add_parser(
        "audit",
        help="Audit architectural patterns, security bounds, and execution splits",
        description="Audit code execution splits against .vibetrace/splits.toml baseline.",
    )
    p_audit_sub = p_audit.add_subparsers(dest="audit_target", required=True)

    p_splits = p_audit_sub.add_parser(
        "splits",
        help="Audit cross-module execution bifurcations against version-controlled baseline",
    )
    p_splits.add_argument("--symbol", default=None, help="Filter audit to a specific symbol")
    p_splits.add_argument(
        "--ci",
        action="store_true",
        help="Run in non-interactive CI mode (exit code 1 if unapproved or modified splits exist)",
    )
    p_splits.add_argument(
        "--strict",
        action="store_true",
        help="Fail CI if any split is flagged as RISK",
    )
    p_splits.add_argument(
        "--path", default=".", help="Project root directory path (default: current directory)"
    )
    p_splits.add_argument(
        "--approve",
        action="store_true",
        help="Approve matching split(s) into .vibetrace/splits.toml baseline",
    )
    p_splits.add_argument(
        "--status",
        default="approved",
        choices=["approved", "risk", "ignored"],
        help="Approval status when using --approve (default: approved)",
    )
    p_splits.add_argument(
        "--rationale",
        default="Architectural split verified via CLI",
        help="Rationale saved when approving splits with --approve",
    )
    p_splits.set_defaults(func=cmd_audit_splits)

    p_security = p_audit_sub.add_parser(
        "security",
        help="Audit code for security vulnerabilities against .vibetrace/security.toml baseline",
    )
    p_security.add_argument("--file", default=None, help="Filter audit to a specific file")
    p_security.add_argument(
        "--ci",
        action="store_true",
        help="Run in CI mode (fails on unapproved or modified vulnerabilities)",
    )
    p_security.add_argument(
        "--strict",
        action="store_true",
        help="Fail CI on MEDIUM severity vulnerabilities as well as CRITICAL/HIGH",
    )
    p_security.add_argument(
        "--fail-on",
        default="HIGH",
        choices=["CRITICAL", "HIGH", "MEDIUM", "LOW"],
        help="Minimum severity threshold to trigger non-zero CI exit (default: HIGH)",
    )
    p_security.add_argument(
        "--path", default=".", help="Project root directory path (default: current directory)"
    )
    p_security.add_argument(
        "--update",
        action="store_true",
        help="Force refresh vulnerability intelligence from Google OSV (bypassing 24h cache)",
    )
    p_security.add_argument(
        "--skip-deps",
        action="store_true",
        help="Skip dependency lockfile CVE checking (AST code scan only)",
    )
    p_security.add_argument(
        "--no-taint",
        action="store_true",
        help="Disable call-graph taint reachability escalation",
    )
    p_security.add_argument(
        "--fix",
        action="store_true",
        help="Emit AI remediation prompts and compliant patch patterns for unapproved findings",
    )
    p_security.add_argument(
        "--sarif",
        action="store_true",
        help="Output audit report in OASIS SARIF 2.1.0 JSON format for GitHub Code Scanning / IDEs",
    )
    p_security.add_argument(
        "--include-tests",
        action="store_true",
        help="Include test directories and test files in AST security scanning",
    )
    p_security.add_argument(
        "--approve",
        action="store_true",
        help="Approve current unapproved findings into .vibetrace/security.toml baseline",
    )
    p_security.add_argument(
        "--rationale",
        default="Security review verified via CLI",
        help="Rationale saved when approving security exceptions with --approve",
    )
    p_security.add_argument(
        "--tag",
        default="risk-accepted",
        choices=["no-alternative", "risk-accepted", "false-positive", "compensating-control"],
        help="Disposition tag saved when approving exceptions with --approve",
    )
    p_security.set_defaults(func=cmd_audit_security)

    p_quality = p_audit_sub.add_parser(
        "quality",
        help="Audit code for cognitive complexity and clean code smells",
    )
    p_quality.add_argument(
        "--file", default=None, help="Filter audit to a specific file or directory"
    )
    p_quality.add_argument(
        "--fix",
        action="store_true",
        help="Apply automated safe fixes via Ruff and Biome sub-engines",
    )
    p_quality.add_argument(
        "--update",
        action="store_true",
        help="Force refresh quality rule definitions from remote catalog",
    )
    p_quality.add_argument(
        "--ci",
        action="store_true",
        help="Run in CI mode (fails on unapproved or modified quality issues)",
    )
    p_quality.add_argument(
        "--strict",
        action="store_true",
        help="Fail CI on MEDIUM severity issues (e.g. S1066 mergeable ifs, S134 deep nesting)",
    )
    p_quality.add_argument(
        "--fail-on",
        default="HIGH",
        choices=["HIGH", "MEDIUM", "LOW"],
        help="Minimum severity threshold to trigger non-zero CI exit (default: HIGH)",
    )
    p_quality.add_argument(
        "--threshold",
        type=int,
        default=15,
        help="Maximum allowable cognitive complexity score per function (default: 15)",
    )
    p_quality.add_argument(
        "--path", default=".", help="Project root directory path (default: current directory)"
    )
    p_quality.add_argument(
        "--sarif",
        action="store_true",
        help="Output audit report in OASIS SARIF 2.1.0 JSON format for GitHub Code Scanning / IDEs",
    )
    p_quality.add_argument(
        "--include-tests",
        action="store_true",
        help="Include test directories and test files in quality audit",
    )
    p_quality.add_argument(
        "--typecheck",
        action="store_true",
        help="Run incremental TypeScript compiler (tsc) type-check pass across TS/TSX files",
    )
    p_quality.add_argument(
        "--approve",
        action="store_true",
        help="Approve current unapproved findings into .vibetrace/quality.toml baseline",
    )
    p_quality.add_argument(
        "--rationale",
        default="Code quality review verified via CLI",
        help="Rationale saved when approving quality exceptions with --approve",
    )
    p_quality.add_argument(
        "--tag",
        default="intentional",
        choices=["intentional", "legacy", "perf-critical"],
        help="Disposition tag saved when approving exceptions with --approve",
    )
    p_quality.set_defaults(func=cmd_audit_quality)

    # audit fix-deps
    p_fix_deps = p_audit_sub.add_parser(
        "fix-deps",
        help="Automatically patch vulnerable dependencies or mitigate unpatched CVEs",
        description=(
            "Resolve minimal safe versions for package CVEs and run native package "
            "managers. For unpatched CVEs, performs AST reachability analysis and "
            "proposes call-site guards or baseline suppressions."
        ),
    )
    p_fix_deps.add_argument(
        "--apply",
        action="store_true",
        default=False,
        help="Apply package upgrades via native package manager (uv, bun, cargo, go)",
    )
    p_fix_deps.add_argument(
        "--dry-run",
        action="store_true",
        default=True,
        help="Preview patch plan and reachability audit without modifying files (default: True)",
    )
    p_fix_deps.add_argument(
        "--package",
        default=None,
        help="Filter remediation to a single specific package name",
    )
    p_fix_deps.add_argument(
        "--suppress-unpatched",
        action="store_true",
        default=False,
        help="Automatically record baseline exceptions for verified uncalled unpatched CVEs",
    )
    p_fix_deps.add_argument(
        "--path", default=".", help="Project root directory path (default: current directory)"
    )
    p_fix_deps.set_defaults(func=cmd_audit_fix_deps)

    # audit pr-taint
    p_pr_taint = p_audit_sub.add_parser(
        "pr-taint",
        help="Run PR-scoped taint analysis with inline remediation suggestions",
        description=(
            "Audits Git PR diffs for inter-procedural taint reachability, synthesizes "
            "ready-to-merge safe code replacements, and formats inline GitHub suggestions."
        ),
    )
    p_pr_taint.add_argument(
        "--base",
        default="main",
        help="Base branch/commit to diff against (default: main)",
    )
    p_pr_taint.add_argument(
        "--staged",
        action="store_true",
        default=False,
        help="Analyze staged changes only instead of branch diff",
    )
    p_pr_taint.add_argument(
        "--pr",
        type=int,
        default=None,
        help="GitHub Pull Request number for online review comments",
    )
    p_pr_taint.add_argument(
        "--post",
        action="store_true",
        default=False,
        help="Submit inline review comments to GitHub PR using GITHUB_TOKEN",
    )
    p_pr_taint.add_argument(
        "--apply",
        action="store_true",
        default=False,
        help="Automatically apply synthesized safe code replacements to local files",
    )
    p_pr_taint.add_argument(
        "--output-md",
        default=None,
        help="Write GitHub-flavored markdown review summary to specified file path",
    )
    p_pr_taint.add_argument(
        "--path", default=".", help="Project root directory path (default: current directory)"
    )
    p_pr_taint.set_defaults(func=cmd_audit_pr_taint)

    # review (unified AI code review gate)
    p_review = sub.add_parser(
        "review",
        help="Run unified 1-call AI code review (Security + Split Audit + Caller Guard)",
    )
    p_review.add_argument(
        "path", nargs="?", default=".", help="Project root path (default: current directory)"
    )
    p_review.add_argument(
        "--full",
        action="store_true",
        help="Review the entire repository instead of only git-changed files",
    )
    p_review.add_argument(
        "--deps",
        "--check-deps",
        dest="deps",
        action="store_true",
        help="Include OSV dependency lockfile CVE checks in the review",
    )
    p_review.add_argument(
        "--ci",
        action="store_true",
        help="Exit with code 1 if any unapproved or modified security/split issues exist",
    )
    p_review.add_argument(
        "--min-coverage",
        type=float,
        default=None,
        help="Minimum test coverage percentage for modified symbols (default: 80.0)",
    )
    p_review.add_argument(
        "--strict",
        action="store_true",
        help="Enforce test coverage threshold as a hard fail in the Quality Gate",
    )
    p_review.add_argument(
        "--sarif",
        dest="sarif",
        default=None,
        help="Optional path to external SARIF file (Semgrep, Sonar, CodeQL) to ingest and enrich",
    )
    p_review.add_argument(
        "--typecheck",
        action="store_true",
        help="Run incremental TypeScript compiler (tsc) type-check pass during review",
    )
    p_review.set_defaults(func=cmd_review)

    # diagnose
    p_diagnose = sub.add_parser(
        "diagnose",
        help="360° symbol diagnosis (slice, callers, blast radius, specs, tests)",
        description=(
            "Consolidated agentic diagnosis: inspects source slice, callee contracts, "
            "incoming callers with line numbers, transitive blast radius, linked documentation, "
            "and sibling test fixtures in 1 round-trip."
        ),
    )
    p_diagnose.add_argument("symbol", help="Target symbol name to diagnose")
    p_diagnose.add_argument(
        "--depth", type=int, default=2, help="Blast radius traversal depth (default: 2)"
    )
    p_diagnose.add_argument(
        "--no-code", action="store_true", help="Exclude source code slice from output"
    )
    p_diagnose.set_defaults(func=cmd_diagnose)

    # tools (sub-engine health status and on-demand provisioning)
    p_tools = sub.add_parser(
        "tools",
        help="Manage polyglot quality sub-engines (status, doctor, install)",
        description=(
            "Check availability of ecosystem linters (Ruff, Biome, Clippy, PHPStan) "
            "and provision standalone runners on demand."
        ),
    )
    p_tools.add_argument(
        "action",
        nargs="?",
        default="status",
        choices=["status", "doctor", "list", "install"],
        help="Action to perform (status, doctor, list, install; default: status)",
    )
    p_tools.add_argument(
        "language",
        nargs="?",
        default="",
        help="Target language for install (e.g. php, python, typescript, go, rust, or all)",
    )
    p_tools.add_argument(
        "--path",
        default=".",
        help="Target project path for local tool provisioning (default: .)",
    )
    p_tools.set_defaults(func=cmd_tools)

    # config (sub-engine presets and settings)
    register_config_parser(sub)

    # project (federated multi-repo workspace)
    register_project_parser(sub)

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
