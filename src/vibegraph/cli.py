"""Unified VibeGraph CLI for AI Agents and Developers."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from vibegraph.indexer.embeddings import EmbeddingEngine
from vibegraph.indexer.main import reindex_all
from vibegraph.mcp.formatters import (
    format_code_slice_md,
    format_semantic_search_md,
    format_structural_summary_md,
    format_trace_path_md,
)
from vibegraph.services.graph_service import (
    compute_impact_analysis,
    get_code_slice,
    get_context_for_path,
    normalize_path,
    notify_ui_focus,
    safe_str,
    trace_execution_path,
)
from vibegraph.services.hybrid_search import hybrid_search


def _cmd_index(args: argparse.Namespace) -> int:
    target = str(Path(args.path).resolve())
    db, root = get_context_for_path(target)
    reindex_all(db, target, verbose=not args.json, force=args.force, project_root=root)

    embedded_count = 0
    if args.embed:
        engine = EmbeddingEngine()
        if not engine.is_available():
            msg = (
                "Optional [embeddings] extra not installed. "
                "Install with: uv pip install -e '.[embeddings]'"
            )
            if args.json:
                print(json.dumps({"status": "indexed", "embeddings_error": msg}))
                return 0
            print(f"Warning: {msg}", file=sys.stderr)
        else:
            embedded_count = engine.index_missing_embeddings(db)
            if not args.json:
                print(f"Encoded {embedded_count} symbol embeddings (256d Matryoshka).")

    if args.json:
        print(
            json.dumps(
                {
                    "status": "ok",
                    "path": target,
                    "embedded_count": embedded_count,
                },
                indent=2,
            )
        )
    return 0


def _cmd_search(args: argparse.Namespace) -> int:
    db, _ = get_context_for_path(args.path)
    results = hybrid_search(db, args.query, limit=args.limit)
    notify_ui_focus(
        "semantic_search",
        [r.node_id for r in results[:5]],
        summary=f"CLI search '{args.query}'",
    )
    if args.json:
        print(json.dumps([r.model_dump() for r in results], indent=2))
    else:
        print(safe_str(format_semantic_search_md(args.query, results)))
    return 0


def _cmd_trace(args: argparse.Namespace) -> int:
    db, _ = get_context_for_path(args.path)
    res = trace_execution_path(db, args.from_symbol, args.to_symbol, max_depth=args.depth)
    if res.found:
        notify_ui_focus(
            "trace_path",
            res.node_ids,
            summary=f"CLI trace {args.from_symbol} -> {args.to_symbol}",
            from_symbol=args.from_symbol,
            to_symbol=args.to_symbol,
        )
    if args.json:
        print(res.model_dump_json(indent=2))
    else:
        print(safe_str(format_trace_path_md(res)))
    return 0 if res.found else 1


def _cmd_slice(args: argparse.Namespace) -> int:
    db, root = get_context_for_path(args.file or args.path)
    res = get_code_slice(
        db,
        root,
        args.symbol,
        file_path=args.file,
        include_callees=not args.no_callees,
    )
    if res.found and res.node_id:
        notify_ui_focus("code_slice", [res.node_id], summary=f"CLI slice {res.qualified_name}")
    if args.json:
        print(res.model_dump_json(indent=2))
    else:
        print(safe_str(format_code_slice_md(res)))
    return 0 if res.found else 1


def _cmd_impact(args: argparse.Namespace) -> int:
    db, root = get_context_for_path(args.file_path)
    norm_path = normalize_path(args.file_path, root)
    total_impact, impacts_by_level = compute_impact_analysis(db, norm_path)
    file_nodes = db.get_nodes_in_file(norm_path)
    notify_ui_focus(
        "impact_analysis",
        [n["id"] for n in file_nodes],
        summary=f"CLI impact {norm_path}",
    )

    if args.json:
        print(
            json.dumps(
                {
                    "file_path": norm_path,
                    "total_impact": total_impact,
                    "levels": impacts_by_level,
                },
                indent=2,
            )
        )
        return 0

    lines = [f"## Impact Analysis for `{norm_path}`"]
    if total_impact == 0:
        lines.append(safe_str("✅ No external dependencies found."))
    else:
        lines.append(f"**Total Impact**: {total_impact} components affected.\n")
        for lvl in (1, 2, 3):
            if impacts_by_level.get(lvl):
                lines.append(f"### Level {lvl}")
                lines.extend(sorted(set(impacts_by_level[lvl])))
    print("\n".join(lines))
    return 0


def _cmd_summary(args: argparse.Namespace) -> int:
    db, root = get_context_for_path(args.file_path)
    norm_path = normalize_path(args.file_path, root)
    total, rows = db.get_file_structure(norm_path, args.limit, args.offset)
    if args.json:
        print(json.dumps({"file_path": norm_path, "total": total, "nodes": rows}, indent=2))
    else:
        print(
            safe_str(format_structural_summary_md(norm_path, total, rows, args.limit, args.offset))
        )
    return 0


def _cmd_serve(args: argparse.Namespace) -> int:
    import uvicorn

    uvicorn.run("vibegraph.server_api:app", host=args.host, port=args.port)
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="vibegraph",
        description="VibeGraph Code Intelligence CLI (AST + FTS5 BM25 + EmbeddingGemma 2)",
    )
    parser.add_argument("--root", default=None, help="Project root path override")
    parser.add_argument("--json", action="store_true", help="Emit JSON output")
    sub = parser.add_subparsers(dest="command", required=True)

    # index
    p_idx = sub.add_parser("index", help="Index repository AST, FTS5, and optional embeddings")
    p_idx.add_argument("path", nargs="?", default=".", help="Directory or file path to index")
    p_idx.add_argument("--embed", action="store_true", help="Compute EmbeddingGemma 2 256d vectors")
    p_idx.add_argument("--force", action="store_true", help="Force re-indexing unchanged files")
    p_idx.add_argument("--json", action="store_true", default=False, help="Emit JSON output")
    p_idx.set_defaults(func=_cmd_index)

    # search
    p_search = sub.add_parser("search", help="Hybrid RRF search across AST, FTS5, and vectors")
    p_search.add_argument("query", help="Search query or natural language intent")
    p_search.add_argument("--limit", type=int, default=15, help="Max results (default: 15)")
    p_search.add_argument("--path", default=".", help="Project root path")
    p_search.add_argument("--json", action="store_true", default=False, help="Emit JSON output")
    p_search.set_defaults(func=_cmd_search)

    # trace
    p_trace = sub.add_parser("trace", help="Bidirectional BFS execution tracepath between symbols")
    p_trace.add_argument("from_symbol", help="Source symbol name")
    p_trace.add_argument("to_symbol", help="Target symbol name")
    p_trace.add_argument("--depth", type=int, default=6, help="Max hop depth (default: 6)")
    p_trace.add_argument("--path", default=".", help="Project root path")
    p_trace.add_argument("--json", action="store_true", default=False, help="Emit JSON output")
    p_trace.set_defaults(func=_cmd_trace)

    # slice
    p_slice = sub.add_parser("slice", help="Read exact symbol source lines and callee contracts")
    p_slice.add_argument("symbol", help="Symbol name or ClassName.method_name")
    p_slice.add_argument("--file", default=None, help="Optional file path to disambiguate")
    p_slice.add_argument("--no-callees", action="store_true", help="Omit callee contracts")
    p_slice.add_argument("--path", default=".", help="Project root path")
    p_slice.add_argument("--json", action="store_true", default=False, help="Emit JSON output")
    p_slice.set_defaults(func=_cmd_slice)

    # impact
    p_impact = sub.add_parser("impact", help="Compute 3-level blast radius for a file")
    p_impact.add_argument("file_path", help="File path to analyze")
    p_impact.add_argument("--json", action="store_true", default=False, help="Emit JSON output")
    p_impact.set_defaults(func=_cmd_impact)

    # summary
    p_sum = sub.add_parser("summary", help="Get structural outline of a file")
    p_sum.add_argument("file_path", help="File path to summarize")
    p_sum.add_argument("--limit", type=int, default=100, help="Max symbols")
    p_sum.add_argument("--offset", type=int, default=0, help="Pagination offset")
    p_sum.add_argument("--json", action="store_true", default=False, help="Emit JSON output")
    p_sum.set_defaults(func=_cmd_summary)

    # serve
    p_serve = sub.add_parser("serve", help="Start the VibeGraph API & Map Room WebSocket server")
    p_serve.add_argument("--host", default="127.0.0.1", help="Bind host")
    p_serve.add_argument("--port", type=int, default=8000, help="Bind port")
    p_serve.set_defaults(func=_cmd_serve)

    return parser


def main(argv: list[str] | None = None) -> int:
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
