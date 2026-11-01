"""CLI command execution handlers for documentation catalogs and RAG."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any, Literal

from vibetrace.docs.db import get_global_doc_db, get_local_doc_db
from vibetrace.docs.service import DocCatalogManager, DocIndexService, DocSearchService


def cmd_docs(args: argparse.Namespace) -> int:
    """Entrypoint for `vibetrace docs ...` subcommands."""
    docs_action = getattr(args, "docs_action", None)
    if not docs_action:
        print("Usage: vibetrace docs {catalog,index,search} ...", file=sys.stderr)
        return 1

    if docs_action == "catalog":
        return _cmd_docs_catalog(args)
    elif docs_action == "index":
        return _cmd_docs_index(args)
    elif docs_action == "search":
        return _cmd_docs_search(args)
    return 0


def _catalog_add(args: argparse.Namespace, root: Path, is_global: bool) -> int:
    name = getattr(args, "name", None)
    path_str = getattr(args, "path", None)
    if not name or not path_str:
        print("Error: catalog name and path are required for `catalog add`", file=sys.stderr)
        return 1
    DocCatalogManager.add_path(name, path_str, global_scope=is_global, project_root=root)
    if getattr(args, "json", False):
        print(
            json.dumps({"status": "added", "catalog": name, "path": path_str, "global": is_global})
        )
    else:
        scope_str = "global (~/.vibetrace/config.toml)" if is_global else "local (.vibetrace.toml)"
        print(f"Added '{path_str}' to {scope_str} catalog '{name}'.")
    return 0


def _catalog_remove(args: argparse.Namespace, root: Path, is_global: bool) -> int:
    name = getattr(args, "name", None)
    if not name:
        print("Error: catalog name is required for `catalog remove`", file=sys.stderr)
        return 1
    ok = DocCatalogManager.remove_catalog(name, global_scope=is_global, project_root=root)
    if getattr(args, "json", False):
        print(json.dumps({"status": "removed" if ok else "not_found", "catalog": name}))
    else:
        if ok:
            print(f"Removed catalog '{name}'.")
        else:
            print(f"Catalog '{name}' not found.", file=sys.stderr)
    return 0 if ok else 1


def _catalog_list(args: argparse.Namespace, root: Path) -> int:
    cats = DocCatalogManager.list_catalogs(project_root=root)
    if getattr(args, "json", False):
        print(json.dumps([c.model_dump() for c in cats], indent=2))
        return 0

    if not cats:
        print(
            "No documentation catalogs configured. Use `vibetrace docs catalog add <name> <path>`"
        )
        return 0
    print(f"Documentation Catalogs ({len(cats)}):")
    for c in cats:
        print(f"  • {c.name} [{c.scope}] ({c.doc_count} docs, {c.chunk_count} chunks)")
        for p in c.paths:
            print(f"      - {p}")
    return 0


def _cmd_docs_catalog(args: argparse.Namespace) -> int:
    cat_action = getattr(args, "catalog_action", "list")
    is_global = getattr(args, "global_scope", False)
    root = Path(args.root).resolve() if getattr(args, "root", None) else Path.cwd().resolve()

    if cat_action == "add":
        return _catalog_add(args, root, is_global)
    if cat_action == "remove":
        return _catalog_remove(args, root, is_global)
    return _catalog_list(args, root)


def _cmd_docs_index(args: argparse.Namespace) -> int:
    is_global = getattr(args, "global_scope", False)
    target_catalog = getattr(args, "catalog", None)
    force = getattr(args, "force", False)

    root = Path(args.root).resolve() if getattr(args, "root", None) else Path.cwd().resolve()
    cats = DocCatalogManager.list_catalogs(project_root=root)

    if target_catalog:
        cats = [c for c in cats if c.name == target_catalog]
        if not cats:
            print(f"Error: catalog '{target_catalog}' not found.", file=sys.stderr)
            return 1

    if is_global:
        cats = [c for c in cats if c.scope == "global"]

    total_files = 0
    total_chunks = 0
    results: dict[str, Any] = {}

    for c in cats:
        db = get_global_doc_db() if c.scope == "global" else get_local_doc_db(root)
        base_dir = Path.home() if c.scope == "global" else root
        res = DocIndexService.index_catalog(c.name, c.paths, db, base_dir, force=force)
        results[c.name] = res
        total_files += res["files"]
        total_chunks += res["chunks"]

    if getattr(args, "json", False):
        print(
            json.dumps(
                {
                    "status": "ok",
                    "total_files": total_files,
                    "total_chunks": total_chunks,
                    "catalogs": results,
                },
                indent=2,
            )
        )
    else:
        print(
            f"Indexed {total_files} doc files ({total_chunks} chunks) across {len(cats)} catalogs."
        )
    return 0


def _cmd_docs_search(args: argparse.Namespace) -> int:
    query = getattr(args, "query", "")
    if not query:
        print("Error: search query string is required", file=sys.stderr)
        return 1

    catalog = getattr(args, "catalog", None)
    raw_scope = getattr(args, "scope", "local")
    scope: Literal["all", "local", "global"] = (
        raw_scope if raw_scope in ("all", "local", "global") else "local"
    )
    limit = getattr(args, "limit", 5)

    root = Path(args.root).resolve() if getattr(args, "root", None) else Path.cwd().resolve()
    items = DocSearchService.search(
        query=query,
        catalog=catalog,
        scope=scope,
        project_root=root,
        limit=limit,
    )

    if getattr(args, "json", False):
        print(json.dumps([it.model_dump() for it in items], indent=2))
    else:
        if not items:
            hint = " (scope: local). Try --scope global or --scope all." if scope == "local" else ""
            print(f"No documentation matches found for '{query}'{hint}")
            return 0
        print(f"Found {len(items)} documentation results for '{query}':\n")
        for idx, it in enumerate(items, start=1):
            sources = ", ".join(it.match_sources)
            header = (
                f"{idx}. [{it.scope.upper()}:{it.catalog}] {it.file_path} "
                f"(L{it.start_line}-L{it.end_line}) | {it.heading_path}"
            )
            print(header)
            print(f"   Score: {it.score:.4f} via [{sources}]")
            preview = it.content[:200].replace("\n", " ")
            print(f"   {preview}...\n")
    return 0
