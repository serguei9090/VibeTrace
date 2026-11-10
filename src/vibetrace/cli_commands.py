"""CLI command execution handlers for VibeTrace."""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

from vibetrace.cli_docs import cmd_docs

__all__ = ["cmd_docs"]
from vibetrace.indexer.db import IndexerDB
from vibetrace.indexer.embeddings import EmbeddingEngine
from vibetrace.indexer.hooks import get_hooks_status, install_hooks, uninstall_hooks
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
    analyze_file_dependencies,
    build_call_stack,
    build_call_tree,
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

    if getattr(args, "scip", None):
        from vibetrace.indexer.scip_importer import ScipImporter

        scip_file = Path(args.scip).resolve()
        stats = ScipImporter.import_scip_file(scip_file, db, Path(root))
        if args.json:
            print(json.dumps({"status": "ok", "scip": stats}, indent=2))
        else:
            print(
                f"✅ Imported SCIP index from `{scip_file.name}`: "
                f"{stats['nodes_imported']} symbols, {stats['edges_imported']} references "
                f"across {stats['files_processed']} file(s)."
            )
        return 0

    from vibetrace.services.reindex_service import ReindexService

    res = ReindexService.execute_reindex(
        db=db,
        project_root=Path(root),
        force=args.force,
        embed=args.embed,
        docs=not getattr(args, "no_docs", False),
        use_lsp=args.lsp,
        verbose=not args.json,
        is_json=args.json,
    )

    if args.json:
        payload = {
            "status": "ok",
            "path": target,
            "embedded_count": res.get("embedded_count", 0),
            "docs": res.get("doc_stats", {}),
            "telemetry": res.get("telemetry", {}),
        }
        if "embeddings_error" in res:
            payload["embeddings_error"] = res["embeddings_error"]
        print(json.dumps(payload, indent=2))
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
    if not args.json and not engine.is_available():
        print(
            "💡 Note: Running lexical BM25 + graph ranking. "
            "For 256d vector hybrid search, install: vibetrace[embeddings]\n"
        )
    results = hybrid_search(
        db,
        args.query,
        limit=args.limit,
        offset=getattr(args, "offset", 0),
        embedding_engine=engine,
    )
    notify_ui_focus(
        "semantic_search",
        [r.node_id for r in results[:5]],
        summary=f"CLI search '{args.query}'",
    )

    doc_chunks = None
    if getattr(args, "with_docs", False):
        try:
            from vibetrace.docs.service import DocSearchService

            doc_chunks = DocSearchService.search(
                query=args.query,
                project_root=root,
                scope="local",
                limit=3,
            )
        except Exception:
            doc_chunks = None

    if args.json:
        if getattr(args, "with_docs", False):
            payload = {
                "code": [r.model_dump() for r in results],
                "docs": [d.model_dump() for d in doc_chunks] if doc_chunks else [],
            }
            print(json.dumps(payload, indent=2))
        else:
            print(json.dumps([r.model_dump() for r in results], indent=2))
    else:
        print(safe_str(format_semantic_search_md(args.query, results, doc_chunks=doc_chunks)))
    return 0


def _handle_list_saved_traces(db: IndexerDB, args: argparse.Namespace) -> int:
    traces = list_saved_traces(db)
    if args.json:
        print(json.dumps([t.model_dump() for t in traces], indent=2))
    else:
        print(safe_str(format_saved_traces_md(traces)))
    return 0


def _handle_delete_saved_trace(db: IndexerDB, args: argparse.Namespace) -> int:
    deleted = delete_saved_trace_by_name(db, args.delete_saved)
    if not deleted:
        print(f"Error: Saved trace '{args.delete_saved}' not found.", file=sys.stderr)
        return 1
    if args.json:
        print(json.dumps({"status": "deleted", "name": args.delete_saved}))
    else:
        print(f"Deleted saved trace '{args.delete_saved}'.")
    return 0


def _resolve_trace_targets(db: IndexerDB, args: argparse.Namespace) -> tuple[str, str, int] | None:
    from_sym = args.from_symbol
    to_sym = args.to_symbol
    depth = args.depth

    if getattr(args, "saved", None):
        saved = get_saved_trace_by_name(db, args.saved)
        if not saved:
            print(f"Error: Saved trace '{args.saved}' not found.", file=sys.stderr)
            return None
        from_sym, to_sym = saved.from_symbol, saved.to_symbol
        depth = depth or saved.max_depth

    depth = depth or 6

    if not from_sym or not to_sym:
        err = "Error: Specify both from_symbol and to_symbol, or use --saved <name>."
        print(err, file=sys.stderr)
        return None

    return from_sym, to_sym, depth


def cmd_trace(args: argparse.Namespace) -> int:
    auto_sync = not getattr(args, "no_sync", False)
    db, _ = get_context_for_path(
        args.path, auto_sync=auto_sync, root_override=getattr(args, "root", None)
    )

    if getattr(args, "list_saved", False):
        return _handle_list_saved_traces(db, args)

    if getattr(args, "delete_saved", None):
        return _handle_delete_saved_trace(db, args)

    targets = _resolve_trace_targets(db, args)
    if not targets:
        return 1
    from_sym, to_sym, depth = targets

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
        include_docs=not getattr(args, "no_docs", False),
        max_doc_tokens=getattr(args, "max_doc_tokens", 300),
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
    all_relations = getattr(args, "all_relations", False)
    rel_types = None if all_relations else ("calls", "routes")
    found, output, start_nodes = build_call_stack(
        db,
        args.symbol,
        direction=direction,
        max_depth=depth,
        file_path=file_path,
        relation_types=rel_types,
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

    budget = getattr(args, "budget", None)
    if budget is not None:
        from vibetrace.services.repo_map_service import RepoMapService

        res = RepoMapService.generate_budgeted_repo_map(
            db, scope_path=args.file_path, budget_tokens=budget, project_root=Path(root)
        )
        if args.json:
            print(json.dumps(res, indent=2))
        else:
            print(res["formatted_map"])
        return 0

    norm_path = normalize_path(args.file_path, root)
    total, rows = db.get_file_structure(norm_path, args.limit, args.offset)
    if args.json:
        print(json.dumps({"file_path": norm_path, "total": total, "nodes": rows}, indent=2))
    else:
        print(
            safe_str(format_structural_summary_md(norm_path, total, rows, args.limit, args.offset))
        )
    return 0


def _hooks_install(root: Path, args: argparse.Namespace) -> int:
    ok, msg = install_hooks(root, provider=args.provider)
    if args.json:
        print(json.dumps({"status": "ok" if ok else "error", "message": msg}))
    else:
        print(msg)
    return 0 if ok else 1


def _hooks_uninstall(root: Path, args: argparse.Namespace) -> int:
    ok, msg = uninstall_hooks(root)
    if args.json:
        print(json.dumps({"status": "ok" if ok else "error", "message": msg}))
    else:
        print(msg)
    return 0 if ok else 1


def _hooks_status(root: Path, args: argparse.Namespace) -> int:
    st = get_hooks_status(root)
    if args.json:
        print(json.dumps(st, indent=2))
        return 0

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


def cmd_hooks(args: argparse.Namespace) -> int:
    target = str(Path(getattr(args, "root", None) or args.path).resolve())
    _, root = get_context_for_path(
        target, auto_sync=False, root_override=getattr(args, "root", None)
    )

    action = args.action or "status"
    if action == "install":
        return _hooks_install(root, args)
    if action == "uninstall":
        return _hooks_uninstall(root, args)
    return _hooks_status(root, args)


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
    configure_hooks = getattr(args, "hooks", False)

    include_tests = getattr(args, "include_tests", None) or False
    codebase_embeddings = getattr(args, "codebase_embeddings", None)
    compute_device = getattr(args, "device", None) or "auto"
    enable_sonar = getattr(args, "enable_sonar", None)
    enable_semgrep = True
    enable_trivy = True

    # Check if embeddings extra is installed
    has_embeddings_pkg = False
    try:
        import sentence_transformers  # noqa: F401

        has_embeddings_pkg = True
    except ImportError:
        has_embeddings_pkg = False

    non_interactive = getattr(args, "yes", False) or getattr(args, "json", False)
    run_prompts = getattr(args, "interactive", False) or (
        not non_interactive and sys.stdin.isatty() and not (root / ".vibetrace.toml").exists()
    )

    # If non-interactive and embedding not explicitly set, default to False
    if codebase_embeddings is None:
        codebase_embeddings = False
    if enable_sonar is None:
        enable_sonar = False

    if run_prompts and not (root / ".vibetrace.toml").exists():
        try:
            if getattr(args, "include_tests", None) is None:
                ans = (
                    input(
                        "Exclude test files (*test*, tests/**, *.spec.*) from Quality & Security "
                        "gates by default? [Y/n]: "
                    )
                    .strip()
                    .lower()
                )
                if ans in ("n", "no"):
                    include_tests = True

            if getattr(args, "codebase_embeddings", None) is None:
                if has_embeddings_pkg:
                    ans_emb = (
                        input(
                            "Enable codebase vector embeddings by default? "
                            "(Requires ~1.5GB model download) [y/N]: "
                        )
                        .strip()
                        .lower()
                    )
                    if ans_emb in ("y", "yes"):
                        codebase_embeddings = True
                        if getattr(args, "device", None) is None:
                            prompt_msg = (
                                "Target compute device for embeddings? (auto/gpu/cpu) [auto]: "
                            )
                            ans_dev = input(prompt_msg).strip().lower()
                            if ans_dev in ("gpu", "cuda"):
                                compute_device = "gpu"
                            elif ans_dev == "cpu":
                                compute_device = "cpu"
                            else:
                                compute_device = "auto"
                else:
                    # Inform user that embeddings require optional dependencies
                    codebase_embeddings = False

            if getattr(args, "enable_sonar", None) is None:
                ans_sonar = (
                    input(
                        "Enable optional SonarLint/SonarQube Tier-2 Java rules? "
                        "(Requires local Java JVM) [y/N]: "
                    )
                    .strip()
                    .lower()
                )
                if ans_sonar in ("y", "yes"):
                    enable_sonar = True

            # Prompt to confirm or install LSP language servers for deep call resolution
            from vibetrace.indexer.lsp_bridge import get_default_lsp_command
            from vibetrace.tools_manager import ToolsManager

            active_lsp = get_default_lsp_command(root)
            if not active_lsp:
                print("\n🔍 LSP (Language Server Protocol) Deep Call Resolution:")
                print("   No active LSP server was detected for your project.")
                msg_lsp = (
                    "   Install matching LSP server now (pyright, typescript, gopls, rust)? [Y/n]: "
                )
                try:
                    ans_lsp = input(msg_lsp).strip().lower()
                except (EOFError, KeyboardInterrupt):
                    ans_lsp = "n"
                    print()

                if ans_lsp not in ("n", "no"):
                    try:
                        _ok, msg = ToolsManager.install("lsp", root)
                        print(f"   {msg}")
                    except Exception as err:
                        print(f"   Failed to install LSP: {err}")
                else:
                    # Provide language checklist if user wants a specific language LSP
                    print("   Select language to install LSP:")
                    print("   1) Python (pyright)")
                    print("   2) TypeScript / JavaScript (typescript-language-server)")
                    print("   3) Go (gopls)")
                    print("   4) Rust (rust-analyzer)")
                    print("   5) PHP (intelephense)")
                    print("   6) Skip")
                    prompt_choice = "   Enter choices (e.g. 1, 2) or press Enter to skip: "
                    try:
                        choice = input(prompt_choice).strip()
                    except (EOFError, KeyboardInterrupt):
                        choice = ""
                        print()

                    if choice and choice != "6":
                        lang_map = {
                            "1": "python",
                            "2": "typescript",
                            "3": "go",
                            "4": "rust",
                            "5": "php",
                        }
                        for c in choice.replace(" ", "").split(","):
                            if c in lang_map:
                                try:
                                    _ok, msg = ToolsManager.install(lang_map[c], root)
                                    print(f"   [{lang_map[c]}] {msg}")
                                except Exception as err:
                                    print(f"   [{lang_map[c]}] Failed to install LSP: {err}")
        except (EOFError, KeyboardInterrupt):
            print()

    res = initialize_workspace(
        root,
        no_index=no_index,
        global_skill=global_skill,
        configure_hooks=configure_hooks,
        include_tests=include_tests,
        codebase_embeddings=codebase_embeddings,
        compute_device=compute_device,
        enable_sonar=enable_sonar,
        enable_semgrep=enable_semgrep,
        enable_trivy=enable_trivy,
        custom_skills_dir=getattr(args, "skills_dir", None),
    )

    if getattr(args, "project", False):
        from vibetrace.federation.project import ProjectWorkspace

        ws = ProjectWorkspace.init(root, name=root.name, auto_discover=True)
        res["federation_initialized"] = True
        res["project_name"] = ws.manifest.name
        res["members"] = [m.model_dump() for m in ws.manifest.members]

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
    if res.get("ecosystems"):
        lines.append(f"- **Detected Ecosystems**: {', '.join(res['ecosystems'])}")
    if res.get("doc_catalog_registered"):
        chunks_count = res.get("doc_chunks_indexed", 0)
        lines.append(f"- **Docs Catalog**: 'core' (docs/) [{chunks_count} chunks indexed]")
    if res.get("hooks_installed"):
        lines.append(f"- **Git Pre-Commit Guard**: {res.get('hooks_message', 'configured')}")
    if res["installed_skills"]:
        lines.append(f"- **Skill Installed**: `{', '.join(res['installed_skills'])}`")
    if res.get("federation_initialized"):
        mem_count = len(res.get("members", []))
        proj_msg = f"- **Federated Project**: '{res['project_name']}'"
        lines.append(f"{proj_msg} ({mem_count} member repos in .vibetrace-project.toml)")
    if res.get("semgrep_available"):
        lines.append("- **SAST Security**: Native AST + Semgrep CLI active")
    else:
        lines.append(
            "- **SAST Security**: Native AST active "
            "(💡 run `vibetrace tools install semgrep` for external Semgrep SAST)"
        )
    if res.get("has_containers"):
        if res.get("trivy_available"):
            lines.append(
                "- **Container & K8s Security**: Trivy active (OS container CVE scanning enabled)"
            )
        else:
            lines.append(
                "- **Container & K8s Security**: Native IaC "
                "(💡 run `vibetrace tools install trivy` for deep OS CVEs)"
            )
    if res["indexed"]:
        lines.append(
            f"- **Initial Index**: {res['node_count']} symbols, {res['edge_count']} relations"
        )
    lines.extend(
        [
            "\nNext steps:",
            '  vibetrace search "<query>"',
            "  vibetrace bundle <symbol>",
            "  vibetrace guard --staged",
            "  vibetrace serve",
        ]
    )
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
    analysis = analyze_file_dependencies(db, norm_path)
    file_nodes = db.get_nodes_in_file(norm_path)
    notify_ui_focus(
        "dependencies", [n["id"] for n in file_nodes], summary=f"CLI dependencies {norm_path}"
    )

    reverse_only = getattr(args, "reverse", False) or getattr(args, "dependents", False)

    if args.json:
        counts = {
            "internal": len(analysis["internal"]),
            "third_party": len(analysis["third_party"]),
            "stdlib": len(analysis["stdlib"]),
            "dependents": len(analysis["dependents"]),
            "circular": len(analysis["circular"]),
            "total_imports": len(analysis["internal"])
            + len(analysis["third_party"])
            + len(analysis["stdlib"]),
        }
        payload = {
            "file_path": norm_path,
            "counts": counts,
            **analysis,
        }
        print(json.dumps(payload, indent=2))
    else:
        print(
            safe_str(
                format_dependencies_md(
                    norm_path,
                    analysis["internal"],
                    analysis["third_party"],
                    analysis["stdlib"],
                    dependents=analysis["dependents"],
                    circular=analysis["circular"],
                    reverse_only=reverse_only,
                )
            )
        )
    return 0


def cmd_test(args: argparse.Namespace) -> int:
    """CLI handler for predictive test selection based on modified AST symbols."""
    auto_sync = not getattr(args, "no_sync", False)
    db, root = get_context_for_path(
        args.path, auto_sync=auto_sync, root_override=getattr(args, "root", None)
    )

    from vibetrace.services.predictive_test_service import PredictiveTestService

    target_symbols = [args.symbol] if getattr(args, "symbol", None) else None
    staged = getattr(args, "staged", False)
    dry_run = getattr(args, "dry_run", False)
    runner_override = getattr(args, "runner", None)

    report = PredictiveTestService.resolve_impacted_tests(
        db=db,
        project_root=root,
        staged=staged,
        target_symbols=target_symbols,
    )

    if getattr(args, "json", False):
        print(report.model_dump_json(indent=2))
        return 0

    return PredictiveTestService.execute_impacted_tests(
        report=report, dry_run=dry_run, runner_override=runner_override
    )


def cmd_diagnose(args: argparse.Namespace) -> int:
    """CLI handler for 360° symbol diagnosis (slice, callers, blast radius, specs, tests)."""
    auto_sync = not getattr(args, "no_sync", False)
    db, root = get_context_for_path(
        getattr(args, "symbol", "."),
        auto_sync=auto_sync,
        root_override=getattr(args, "root", None),
    )
    from vibetrace.services.diagnose_service import DiagnoseService, format_diagnosis_md

    report = DiagnoseService.diagnose_symbol(
        db=db,
        project_root=root,
        symbol_name=args.symbol,
        max_depth=getattr(args, "depth", 2),
        include_code=not getattr(args, "no_code", False),
    )

    if getattr(args, "json", False):
        print(report.model_dump_json(indent=2))
        return 0 if report.found else 1

    if not report.found:
        print(f"❌ {report.safety_advisory}")
        return 1

    print(safe_str(format_diagnosis_md(report)))
    return 0
