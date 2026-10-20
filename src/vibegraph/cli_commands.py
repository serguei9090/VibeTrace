"""CLI command execution handlers for VibeGraph."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from vibegraph.indexer.embeddings import EmbeddingEngine
from vibegraph.indexer.hooks import get_hooks_status, install_hooks, uninstall_hooks
from vibegraph.indexer.main import reindex_all
from vibegraph.mcp.formatters import (
    format_code_slice_md,
    format_semantic_search_md,
    format_structural_summary_md,
    format_trace_path_md,
)
from vibegraph.services.graph_service import (
    build_call_tree,
    compute_impact_analysis,
    format_call_tree_md,
    get_code_slice,
    get_context_for_path,
    normalize_path,
    notify_ui_focus,
    safe_str,
    trace_execution_path,
)
from vibegraph.services.hybrid_search import hybrid_search
from vibegraph.services.saved_traces_service import (
    SavedTraceCreate,
    create_saved_trace,
    get_saved_trace_by_name,
    list_saved_traces,
)


def cmd_index(args: argparse.Namespace) -> int:
    target = str(Path(args.path).resolve())
    db, root = get_context_for_path(
        target, auto_sync=False, root_override=getattr(args, "root", None)
    )
    reindex_all(
        db,
        target,
        verbose=not args.json,
        force=args.force,
        project_root=root,
        use_lsp=args.lsp,
    )

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


def cmd_search(args: argparse.Namespace) -> int:
    db, _ = get_context_for_path(args.path, root_override=getattr(args, "root", None))
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


def cmd_trace(args: argparse.Namespace) -> int:
    db, _ = get_context_for_path(args.path, root_override=getattr(args, "root", None))

    if getattr(args, "list_saved", False):
        traces = list_saved_traces(db)
        if args.json:
            print(json.dumps([t.model_dump() for t in traces], indent=2))
        else:
            if not traces:
                print("No saved architectural traces found.")
            else:
                print("## Saved Architectural Traces")
                for t in traces:
                    desc = f" — {t.description}" if t.description else ""
                    line = (
                        f"- **{t.name}**: `{t.from_symbol}` -> `{t.to_symbol}` "
                        f"(depth {t.max_depth}){desc}"
                    )
                    print(line)
        return 0

    from_sym = args.from_symbol
    to_sym = args.to_symbol
    depth = args.depth

    if getattr(args, "saved", None):
        saved = get_saved_trace_by_name(db, args.saved)
        if not saved:
            print(f"Error: Saved trace '{args.saved}' not found.", file=sys.stderr)
            return 1
        from_sym = saved.from_symbol
        to_sym = saved.to_symbol
        depth = saved.max_depth

    if not from_sym or not to_sym:
        err = "Error: Specify both from_symbol and to_symbol, or use --saved <name>."
        print(err, file=sys.stderr)
        return 1

    if getattr(args, "save_as", None):
        create_saved_trace(
            db,
            SavedTraceCreate(
                name=args.save_as,
                from_symbol=from_sym,
                to_symbol=to_sym,
                max_depth=depth,
            ),
        )
        if not args.json:
            print(f"Saved trace '{args.save_as}' successfully.")

    engine = getattr(args, "engine", "bfs")
    res = trace_execution_path(db, from_sym, to_sym, max_depth=depth, engine=engine)
    if res.found:
        notify_ui_focus(
            "trace_path",
            res.node_ids,
            summary=f"CLI trace {from_sym} -> {to_sym}",
            from_symbol=from_sym,
            to_symbol=to_sym,
        )
    if args.json:
        print(res.model_dump_json(indent=2))
    else:
        print(safe_str(format_trace_path_md(res)))
    return 0 if res.found else 1


def cmd_slice(args: argparse.Namespace) -> int:
    db, root = get_context_for_path(
        args.file or args.path, root_override=getattr(args, "root", None)
    )
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


def cmd_call_tree(args: argparse.Namespace) -> int:
    db, _ = get_context_for_path(args.path, root_override=getattr(args, "root", None))
    res = build_call_tree(db, args.symbol, max_depth=args.depth, file_path=args.file)
    if res.found and res.node_ids:
        notify_ui_focus(
            "call_tree",
            res.node_ids,
            summary=f"CLI call-tree '{args.symbol}' (depth {args.depth})",
        )
    if args.json:
        print(res.model_dump_json(indent=2))
    else:
        print(safe_str(format_call_tree_md(res)))
    return 0 if res.found else 1


def cmd_impact(args: argparse.Namespace) -> int:
    db, root = get_context_for_path(args.file_path, root_override=getattr(args, "root", None))
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


def cmd_summary(args: argparse.Namespace) -> int:
    db, root = get_context_for_path(args.file_path, root_override=getattr(args, "root", None))
    norm_path = normalize_path(args.file_path, root)
    total, rows = db.get_file_structure(norm_path, args.limit, args.offset)
    if args.json:
        print(json.dumps({"file_path": norm_path, "total": total, "nodes": rows}, indent=2))
    else:
        print(
            safe_str(format_structural_summary_md(norm_path, total, rows, args.limit, args.offset))
        )
    return 0


def cmd_hooks(args: argparse.Namespace) -> int:
    target = str(Path(args.path).resolve())
    _, root = get_context_for_path(target, auto_sync=False)

    action = args.action or "status"
    if action == "install":
        ok, msg = install_hooks(root, provider=args.provider)
        if args.json:
            print(json.dumps({"status": "ok" if ok else "error", "message": msg}))
        else:
            print(msg)
        return 0 if ok else 1
    if action == "uninstall":
        ok, msg = uninstall_hooks(root)
        if args.json:
            print(json.dumps({"status": "ok" if ok else "error", "message": msg}))
        else:
            print(msg)
        return 0 if ok else 1

    st = get_hooks_status(root)
    if args.json:
        print(json.dumps(st, indent=2))
    else:
        print("## VibeGraph Git Hooks Status")
        print(f"- **Detected Provider**: `{st['provider']}`")
        print(
            f"- **Lefthook Configured**: `{st['lefthook']['configured']}` "
            f"(available: {st['lefthook']['available']})"
        )
        print(f"- **Git Post-Commit Hook**: `{st['git']['post_commit']}`")
        print(f"- **Git Post-Merge Hook**: `{st['git']['post_merge']}`")
        if not st["lefthook"]["configured"] and not st["git"]["post_commit"]:
            print("\nTo install automated post-commit hooks, run:\n  vibegraph hooks install")
    return 0


def cmd_serve(args: argparse.Namespace) -> int:
    import uvicorn

    uvicorn.run("vibegraph.server_api:app", host=args.host, port=args.port)
    return 0
