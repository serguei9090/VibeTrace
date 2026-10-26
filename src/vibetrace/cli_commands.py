"""CLI command execution handlers for VibeTrace."""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

from vibetrace.indexer.embeddings import EmbeddingEngine
from vibetrace.indexer.hooks import get_hooks_status, install_hooks, uninstall_hooks
from vibetrace.indexer.main import reindex_all
from vibetrace.mcp.formatters import (
    format_code_slice_md,
    format_dependencies_md,
    format_impact_analysis_md,
    format_references_md,
    format_saved_traces_md,
    format_semantic_search_md,
    format_signature_search_md,
    format_structural_summary_md,
    format_trace_path_md,
)
from vibetrace.services.graph_service import (
    build_call_stack,
    build_call_tree,
    categorize_file_dependencies,
    compute_impact_analysis,
    find_symbol_references,
    format_call_tree_md,
    get_code_slice,
    get_context_for_path,
    normalize_path,
    notify_ui_focus,
    safe_str,
    trace_execution_path,
)
from vibetrace.services.hybrid_search import hybrid_search
from vibetrace.services.saved_traces_service import (
    SavedTraceCreate,
    create_saved_trace,
    delete_saved_trace_by_name,
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
        engine = EmbeddingEngine(project_root=root)
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
            json.dumps({"status": "ok", "path": target, "embedded_count": embedded_count}, indent=2)
        )
    return 0


def cmd_search(args: argparse.Namespace) -> int:
    auto_sync = not getattr(args, "no_sync", False)
    db, root = get_context_for_path(
        args.path, auto_sync=auto_sync, root_override=getattr(args, "root", None)
    )
    if getattr(args, "signature", False):
        rows = db.search_by_signature_pattern(
            args.query, limit=args.limit, offset=getattr(args, "offset", 0)
        )
        notify_ui_focus(
            "signature_search", [r["id"] for r in rows[:5]], summary=f"CLI sig '{args.query}'"
        )
        if args.json:
            print(json.dumps(rows, indent=2))
        else:
            print(safe_str(format_signature_search_md(args.query, rows)))
        return 0

    engine = EmbeddingEngine(project_root=root)
    results = hybrid_search(db, args.query, limit=args.limit, embedding_engine=engine)
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
    auto_sync = not getattr(args, "no_sync", False)
    db, _ = get_context_for_path(
        args.path, auto_sync=auto_sync, root_override=getattr(args, "root", None)
    )

    if getattr(args, "list_saved", False):
        traces = list_saved_traces(db)
        if args.json:
            print(json.dumps([t.model_dump() for t in traces], indent=2))
        else:
            print(safe_str(format_saved_traces_md(traces)))
        return 0

    if getattr(args, "delete_saved", None):
        deleted = delete_saved_trace_by_name(db, args.delete_saved)
        if not deleted:
            print(f"Error: Saved trace '{args.delete_saved}' not found.", file=sys.stderr)
            return 1
        if args.json:
            print(json.dumps({"status": "deleted", "name": args.delete_saved}))
        else:
            print(f"Deleted saved trace '{args.delete_saved}'.")
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
        if depth is None:
            depth = saved.max_depth

    if depth is None:
        depth = 6

    if not from_sym or not to_sym:
        err = "Error: Specify both from_symbol and to_symbol, or use --saved <name>."
        print(err, file=sys.stderr)
        return 1

    if getattr(args, "save_as", None):
        create_saved_trace(
            db,
            SavedTraceCreate(
                name=args.save_as, from_symbol=from_sym, to_symbol=to_sym, max_depth=depth
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
    auto_sync = not getattr(args, "no_sync", False)
    db, root = get_context_for_path(
        args.file or args.path,
        auto_sync=auto_sync,
        root_override=getattr(args, "root", None),
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
    auto_sync = not getattr(args, "no_sync", False)
    db, _ = get_context_for_path(
        args.path, auto_sync=auto_sync, root_override=getattr(args, "root", None)
    )
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
    auto_sync = not getattr(args, "no_sync", False)
    db, root = get_context_for_path(
        args.file_path, auto_sync=auto_sync, root_override=getattr(args, "root", None)
    )
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

    print(safe_str(format_impact_analysis_md(norm_path, total_impact, impacts_by_level)))
    return 0


def cmd_call_stack(args: argparse.Namespace) -> int:
    auto_sync = not getattr(args, "no_sync", False)
    db, _ = get_context_for_path(
        getattr(args, "path", "."),
        auto_sync=auto_sync,
        root_override=getattr(args, "root", None),
    )
    raw_dir = getattr(args, "direction", "both")
    direction = raw_dir if raw_dir in ("up", "down") else "both"
    depth = getattr(args, "depth", 3)
    file_path = getattr(args, "file", None)
    found, output, start_nodes = build_call_stack(
        db, args.symbol, direction=direction, max_depth=depth, file_path=file_path
    )
    if found and start_nodes:
        notify_ui_focus(
            "call_stack",
            [n["id"] for n in start_nodes],
            summary=f"CLI call-stack '{args.symbol}' ({direction}, depth {depth})",
        )
    if getattr(args, "json", False):
        res = {
            "found": found,
            "symbol": args.symbol,
            "direction": direction,
            "depth": depth,
            "call_stack": output,
            "nodes": [n["id"] for n in start_nodes],
        }
        print(json.dumps(res, indent=2))
    else:
        print("\n".join(output))
    return 0 if found else 1


def cmd_summary(args: argparse.Namespace) -> int:
    auto_sync = not getattr(args, "no_sync", False)
    db, root = get_context_for_path(
        args.file_path, auto_sync=auto_sync, root_override=getattr(args, "root", None)
    )
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
    target = str(Path(getattr(args, "root", None) or args.path).resolve())
    _, root = get_context_for_path(
        target, auto_sync=False, root_override=getattr(args, "root", None)
    )

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
        print("## VibeTrace Git Hooks Status")
        print(f"- **Detected Provider**: `{st['provider']}`")
        print(
            f"- **Lefthook Configured**: `{st['lefthook']['configured']}` "
            f"(available: {st['lefthook']['available']})"
        )
        print(f"- **Git Post-Commit Hook**: `{st['git']['post_commit']}`")
        print(f"- **Git Post-Merge Hook**: `{st['git']['post_merge']}`")
        if not st["lefthook"]["configured"] and not st["git"]["post_commit"]:
            print("\nTo install automated post-commit hooks, run:\n  vibetrace hooks install")
    return 0


def cmd_serve(args: argparse.Namespace) -> int:
    import uvicorn

    proj_root = getattr(args, "root", None) or getattr(args, "path", None)
    if proj_root:
        os.environ["VIBETRACE_PROJECT_ROOT"] = str(Path(proj_root).resolve())

    uvicorn.run("vibetrace.server_api:app", host=args.host, port=args.port)
    return 0


def cmd_init(args: argparse.Namespace) -> int:
    from vibetrace.skill_installer import initialize_workspace

    root = Path(getattr(args, "path", ".") or ".").resolve()
    no_index = getattr(args, "no_index", False)
    global_skill = getattr(args, "global_skill", False)

    res = initialize_workspace(root, no_index=no_index, global_skill=global_skill)

    if getattr(args, "json", False):
        print(json.dumps(res, indent=2))
        return 0

    gi_msg = (
        f"added `{', '.join(res['added_ignores'])}`"
        if res["gitignore_updated"]
        else "already configured"
    )
    lines = [
        "🎉 VibeTrace workspace initialized successfully!",
        f"- **Project Root**: `{res['project_root']}`",
        f"- **Configuration**: `{res.get('config_path', 'default')}`",
        f"- **Local Database**: `{res['db_path']}` (isolated per-project)",
        f"- **.gitignore**: {gi_msg}",
    ]
    if res["installed_skills"]:
        lines.append(f"- **Skill Installed**: `{', '.join(res['installed_skills'])}`")
    if res["indexed"]:
        lines.append(
            f"- **Initial Index**: {res['node_count']} symbols, {res['edge_count']} relations"
        )
    lines.extend(["\nNext steps:", '  vibetrace search "<query>"', "  vibetrace serve"])
    print("\n".join(lines))
    return 0


def cmd_skill(args: argparse.Namespace) -> int:
    from vibetrace.skill_installer import install_skill

    root = Path(getattr(args, "root", None) or getattr(args, "path", ".") or ".").resolve()
    paths = install_skill(root, global_install=getattr(args, "global_skill", False))
    if getattr(args, "json", False):
        print(json.dumps({"status": "ok", "installed_paths": [str(p) for p in paths]}))
    else:
        print(f"✅ VibeTrace Navigator skill installed to: {', '.join(str(p) for p in paths)}")
    return 0


def cmd_mcp(args: argparse.Namespace) -> int:
    from vibetrace.mcp.server import main as mcp_main

    root = str(Path(getattr(args, "root", None) or getattr(args, "path", ".") or ".").resolve())
    os.environ["VIBETRACE_PROJECT_ROOT"] = root
    mcp_main()
    return 0


def cmd_references(args: argparse.Namespace) -> int:
    auto_sync = not getattr(args, "no_sync", False)
    db, root = get_context_for_path(
        args.file or getattr(args, "path", "."),
        auto_sync=auto_sync,
        root_override=getattr(args, "root", None),
    )
    clean_sym = args.symbol.strip().removesuffix("()")
    norm_file = normalize_path(args.file, root) if getattr(args, "file", None) else None
    found, targets, summaries = find_symbol_references(
        db, args.symbol, file_path=norm_file, kind=getattr(args, "kind", None)
    )
    if not found:
        if args.json:
            print(
                json.dumps(
                    {"symbol": clean_sym, "found": False, "targets": [], "total_references": 0},
                    indent=2,
                )
            )
        else:
            print(f"Symbol '{args.symbol}' not found in index.")
        return 1

    notify_ui_focus("references", [t["id"] for t in targets], summary=f"CLI references {clean_sym}")

    if args.json:
        total_refs = sum(len(s["references"]) for s in summaries)
        payload = {
            "symbol": clean_sym,
            "found": True,
            "target_count": len(targets),
            "total_references": total_refs,
            "targets": summaries,
        }
        print(json.dumps(payload, indent=2))
    else:
        print(safe_str(format_references_md(args.symbol, summaries)))
    return 0


def cmd_dependencies(args: argparse.Namespace) -> int:
    auto_sync = not getattr(args, "no_sync", False)
    db, root = get_context_for_path(
        args.file_path, auto_sync=auto_sync, root_override=getattr(args, "root", None)
    )
    norm_path = normalize_path(args.file_path, root)
    internal, third_party, stdlib = categorize_file_dependencies(db, norm_path)
    file_nodes = db.get_nodes_in_file(norm_path)
    notify_ui_focus(
        "dependencies", [n["id"] for n in file_nodes], summary=f"CLI dependencies {norm_path}"
    )

    if args.json:
        counts = {
            "internal": len(internal),
            "third_party": len(third_party),
            "stdlib": len(stdlib),
            "total": len(internal) + len(third_party) + len(stdlib),
        }
        payload = {
            "file_path": norm_path,
            "counts": counts,
            "internal": internal,
            "third_party": third_party,
            "stdlib": stdlib,
        }
        print(json.dumps(payload, indent=2))
    else:
        print(safe_str(format_dependencies_md(norm_path, internal, third_party, stdlib)))
    return 0
