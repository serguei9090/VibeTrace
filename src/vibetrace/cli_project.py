"""CLI commands for multi-repository federated project workspaces."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

from vibetrace.federation.project import ProjectWorkspace


def register_project_parser(sub: Any) -> None:
    """Register the `vibetrace project` subcommand hierarchy."""
    p_proj = sub.add_parser(
        "project",
        help="Manage multi-repository federated project workspaces",
        description=(
            "Connect multiple repositories into an integrated project "
            "for cross-repo blast radius and search."
        ),
    )
    proj_sub = p_proj.add_subparsers(dest="project_action", required=True)

    # project init
    p_init = proj_sub.add_parser("init", help="Initialize a federated project workspace")
    p_init.add_argument(
        "--name", default=None, help="Project workspace name (default: directory name)"
    )
    p_init.add_argument(
        "--no-auto-discover",
        action="store_true",
        help="Do not auto-detect child repository directories",
    )

    # project list
    proj_sub.add_parser("list", help="List all member repositories and their index status")

    # project add
    p_add = proj_sub.add_parser("add", help="Add a member repository to the project workspace")
    p_add.add_argument("name", help="Unique identifier for the member repository")
    p_add.add_argument("path", help="Relative or absolute path to the repository directory")
    p_add.add_argument("--description", default="", help="Optional description of repository")

    # project remove
    p_rm = proj_sub.add_parser("remove", help="Remove a member repository from the project")
    p_rm.add_argument("name", help="Identifier of repository to remove")

    # project index
    p_idx = proj_sub.add_parser("index", help="Index member repositories in the project")
    p_idx.add_argument("--repo", default=None, help="Index only a specific member repository")
    p_idx.add_argument("--force", action="store_true", help="Force reindexing of all files")
    p_idx.add_argument("--verbose", action="store_true", help="Enable verbose log output")

    # project search
    p_search = proj_sub.add_parser("search", help="Federated search across member repositories")
    p_search.add_argument("query", help="Symbol or function name to search for")
    p_search.add_argument("--repo", default=None, help="Scope search to a specific repository")
    p_search.add_argument("--limit", type=int, default=20, help="Maximum results to return")

    # project guard
    p_guard = proj_sub.add_parser("guard", help="Cross-repository caller blast radius analysis")
    p_guard.add_argument("symbol", help="Target symbol or function to inspect")
    p_guard.add_argument("--repo", default=None, help="Repository defining the target symbol")

    # project links
    proj_sub.add_parser("links", help="Discover cross-repository HTTP and RPC contract links")

    p_proj.set_defaults(func=cmd_project)


def cmd_project(args: argparse.Namespace) -> int:
    """Execute `vibetrace project` subcommands."""
    action = getattr(args, "project_action", None)
    if not action:
        print(
            "Usage: vibetrace project {init,list,add,remove,index,search,guard} ...",
            file=sys.stderr,
        )
        return 1

    root_arg = getattr(args, "root", None)
    if root_arg:
        root = Path(root_arg).resolve()
        if not root.is_dir():
            print(f"Error: Specified root directory does not exist: {root}", file=sys.stderr)
            return 1
    else:
        root = Path.cwd().resolve()
    as_json = getattr(args, "json", False)

    try:
        if action == "init":
            return _cmd_project_init(args, root, as_json)
        elif action == "list":
            return _cmd_project_list(args, root, as_json)
        elif action == "add":
            return _cmd_project_add(args, root, as_json)
        elif action == "remove":
            return _cmd_project_remove(args, root, as_json)
        elif action == "index":
            return _cmd_project_index(args, root, as_json)
        elif action == "search":
            return _cmd_project_search(args, root, as_json)
        elif action == "guard":
            return _cmd_project_guard(args, root, as_json)
        elif action == "links":
            return _cmd_project_links(args, root, as_json)
        else:
            print(f"Unknown project action: '{action}'", file=sys.stderr)
            return 1
    except Exception as e:
        if as_json:
            print(json.dumps({"error": str(e)}))
        else:
            print(f"Error: {e}", file=sys.stderr)
        return 1


def _cmd_project_init(args: argparse.Namespace, root: Path, as_json: bool) -> int:
    auto_disc = not getattr(args, "no_auto_discover", False)
    name = getattr(args, "name", None)
    ws = ProjectWorkspace.init(root, name=name, auto_discover=auto_disc)

    if as_json:
        print(json.dumps(ws.manifest.model_dump(), indent=2))
    else:
        print(f"✨ Initialized federated project workspace '{ws.manifest.name}' in {root}")
        print(f"   Registered {len(ws.manifest.members)} member repositories:")
        for m in ws.manifest.members:
            desc = f" ({m.description})" if m.description else ""
            print(f"   • {m.name}: {m.path}{desc}")
        print("\nNext step: Run `vibetrace project index` to index member repositories.")
    return 0


def _cmd_project_list(args: argparse.Namespace, root: Path, as_json: bool) -> int:
    ws = ProjectWorkspace.load(root)
    statuses = ws.list_members()

    if as_json:
        print(json.dumps([s.model_dump() for s in statuses], indent=2))
    else:
        storage = ws.manifest.settings.storage_backend
        print(f"📁 Project Workspace: '{ws.manifest.name}' (Storage: {storage})")
        print(f"{'Repository':<20} {'Status':<12} {'Nodes':<10} {'Edges':<10} {'Path'}")
        print("-" * 75)
        for s in statuses:
            stat_str = "Indexed ✓" if s.is_indexed else "Not Indexed ✗"
            print(f"{s.name:<20} {stat_str:<12} {s.node_count:<10} {s.edge_count:<10} {s.path}")
    return 0


def _cmd_project_add(args: argparse.Namespace, root: Path, as_json: bool) -> int:
    ws = ProjectWorkspace.load(root)
    member = ws.add_member(args.name, args.path, description=getattr(args, "description", ""))

    if as_json:
        print(json.dumps({"status": "added", "member": member.model_dump()}))
    else:
        print(f"✓ Added member repository '{member.name}' ({member.path}) to project workspace.")
    return 0


def _cmd_project_remove(args: argparse.Namespace, root: Path, as_json: bool) -> int:
    ws = ProjectWorkspace.load(root)
    ok = ws.remove_member(args.name)
    if not ok:
        print(f"Member repository '{args.name}' was not found in project.", file=sys.stderr)
        return 1

    if as_json:
        print(json.dumps({"status": "removed", "name": args.name}))
    else:
        print(f"✓ Removed member repository '{args.name}' from project workspace.")
    return 0


def _cmd_project_index(args: argparse.Namespace, root: Path, as_json: bool) -> int:
    ws = ProjectWorkspace.load(root)
    repo = getattr(args, "repo", None)
    force = getattr(args, "force", False)
    verbose = getattr(args, "verbose", False)

    if repo:
        if not as_json:
            print(f"Indexing repository '{repo}'...")
        res = ws.index_member(repo, force=force, verbose=verbose)
        if as_json:
            print(json.dumps(res, indent=2))
        else:
            nc = res.get("node_count", 0)
            ec = res.get("edge_count", 0)
            print(f"✓ Indexed {repo}: {nc} nodes, {ec} edges.")
    else:
        if not as_json:
            print(f"Indexing all {len(ws.manifest.members)} member repositories...")
        results = ws.index_all(force=force, verbose=verbose)
        if as_json:
            print(json.dumps(results, indent=2))
        else:
            for r in results:
                if r.get("status") == "indexed":
                    nc = r.get("node_count", 0)
                    ec = r.get("edge_count", 0)
                    print(f"✓ {r['name']}: {nc} nodes, {ec} edges")
                else:
                    print(f"✗ {r['name']}: Error - {r.get('error')}")
    return 0


def _cmd_project_search(args: argparse.Namespace, root: Path, as_json: bool) -> int:
    ws = ProjectWorkspace.load(root)
    repo = getattr(args, "repo", None)
    limit = getattr(args, "limit", 20)
    results = ws.search(args.query, repo=repo, limit=limit)

    if as_json:
        print(json.dumps([r.model_dump() for r in results], indent=2))
    else:
        scope = f"[{repo}]" if repo else "[All Repositories]"
        print(f"🔍 Federated Search {scope} for '{args.query}' (Found {len(results)}):")
        for r in results:
            sig = f" : {r.signature}" if r.signature else ""
            print(f"  • [{r.repo}] {r.kind} {r.name} ({r.file_path}:{r.start_line}){sig}")
    return 0


def _cmd_project_guard(args: argparse.Namespace, root: Path, as_json: bool) -> int:
    ws = ProjectWorkspace.load(root)
    repo = getattr(args, "repo", None)
    report = ws.guard(args.symbol, target_repo=repo)

    if as_json:
        print(json.dumps(report.model_dump(), indent=2))
    else:
        print(f"🛡️ Cross-Repo Blast Radius Guard: '{report.target_symbol}'")
        print(f"   Defined in: [{report.target_repo}] {report.target_file}")
        print(f"   Risk Level: {report.risk_level} (Total Callers: {report.total_callers})")
        print(f"   Advisory:   {report.safety_advisory}\n")

        if report.callers_by_repo:
            print("   Inbound Callers by Repository:")
            for r_name, callers in report.callers_by_repo.items():
                print(f"   📁 Repository [{r_name}] ({len(callers)} callers):")
                for c in callers:
                    line_str = f":{c['call_line']}" if c.get("call_line") else ""
                    print(f"      • {c['caller_symbol']} in {c['caller_file']}{line_str}")
    return 0


def _cmd_project_links(args: argparse.Namespace, root: Path, as_json: bool) -> int:
    ws = ProjectWorkspace.load(root)
    links = ws.discover_cross_repo_links()

    if as_json:
        print(json.dumps([link.model_dump() for link in links], indent=2))
    else:
        print(f"🔗 Discovered {len(links)} Cross-Repository Contract Links:")
        if not links:
            print("   No matching HTTP routes or RPC service contracts detected.")
        for link in links:
            src = f"[{link.source_repo}] {link.source_symbol} ({link.source_file})"
            tgt = f"[{link.target_repo}] {link.target_symbol} ({link.target_file})"
            print(f"  • {src} --[{link.contract_key}]--> {tgt}")
    return 0
