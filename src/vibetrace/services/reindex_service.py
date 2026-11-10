"""Service layer for coordinating workspace and single-file indexing, embeddings, and docs sync."""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

from vibetrace.indexer.db import IndexerDB
from vibetrace.indexer.embeddings import EmbeddingEngine
from vibetrace.indexer.main import index_file, reindex_all
from vibetrace.services.graph_service import normalize_path


class ReindexService:
    """Encapsulates all indexing and cross-catalog synchronization workflows."""

    @staticmethod
    def execute_reindex(
        db: IndexerDB,
        project_root: Path,
        file_path: str | None = None,
        force: bool = False,
        embed: bool | None = None,
        docs: bool = True,
        use_lsp: bool = False,
        verbose: bool = False,
        is_json: bool = False,
    ) -> dict[str, Any]:
        """Atomically coordinates AST parsing, vector encoding, and doc catalog syncing."""
        active_root = project_root.resolve()

        # Mode A: Single-file reindex
        if file_path:
            norm_path = normalize_path(file_path, active_root)
            abs_target = (active_root / norm_path).resolve()
            if not abs_target.is_relative_to(active_root):
                raise PermissionError(
                    f"Access denied: file path '{file_path}' is outside project root."
                )
            if not abs_target.exists():
                raise FileNotFoundError(f"File not found: '{file_path}'")

            indexed = index_file(db, str(abs_target), project_root=active_root, force=force)
            return {
                "status": "ok",
                "mode": "single_file",
                "file_path": norm_path,
                "indexed": indexed,
            }

        # Mode B: Full workspace reindex
        # Resolve effective LSP mode: CLI flag overrides, else check config indexer.lsp_mode
        eff_lsp = use_lsp
        if not eff_lsp:
            try:
                from vibetrace.config import load_config
                from vibetrace.indexer.lsp_bridge import get_default_lsp_command

                cfg = load_config(active_root)
                mode = getattr(cfg.indexer, "lsp_mode", "auto").lower()
                if mode == "always":
                    eff_lsp = True
                elif mode == "auto":
                    # In auto mode, use LSP if an LSP command is available
                    eff_lsp = get_default_lsp_command(active_root) is not None
            except Exception:
                eff_lsp = False

        telemetry = reindex_all(
            db,
            str(active_root),
            verbose=verbose,
            force=force,
            project_root=active_root,
            use_lsp=eff_lsp,
        )
        pruned_edges = db.prune_orphaned_edges()
        if not is_json and verbose and pruned_edges > 0:
            print(f"Cleaned {pruned_edges} orphaned graph edges.")

        purged_embeddings = db.purge_orphaned_embeddings()
        if not is_json and verbose and purged_embeddings > 0:
            print(f"Purged {purged_embeddings} orphaned vector embeddings.")

        # Determine whether embeddings should run:
        # 1. Explicit CLI argument (--embed: True, --no-embed: False)
        # 2. If None, fall back to project configuration (vectors.codebase)
        should_embed = False
        if embed is True:
            should_embed = True
        elif embed is False:
            should_embed = False
        else:
            try:
                from vibetrace.config import load_config

                cfg = load_config(active_root)
                should_embed = bool(cfg.vectors.codebase)
            except Exception:
                should_embed = False

        embedded_count = 0
        embeddings_error = None
        if should_embed:
            engine = EmbeddingEngine(project_root=active_root)
            if not engine.is_available():
                embeddings_error = (
                    "Optional [embeddings] extra not installed. "
                    "Run: uv pip install -e '.[embeddings]'"
                )
                if not is_json:
                    print(f"Warning: {embeddings_error}", file=sys.stderr)
            else:
                embedded_count = engine.index_missing_embeddings(
                    db, verbose=verbose and not is_json
                )
                if not is_json and verbose and embedded_count > 0:
                    print(f"Encoded {embedded_count} symbol embeddings (256d Matryoshka).")

        doc_stats = {"catalogs": 0, "files": 0, "chunks": 0}
        if docs:
            try:
                from vibetrace.docs.db import DocDB
                from vibetrace.docs.service import DocCatalogManager, DocIndexService

                local_cats = [
                    c for c in DocCatalogManager.list_catalogs(active_root) if c.scope == "local"
                ]
                if local_cats:
                    doc_db = DocDB(db.db_path)
                    for cat in local_cats:
                        res_doc = DocIndexService.index_catalog(
                            cat.name, cat.paths, doc_db, active_root, force=force
                        )
                        doc_stats["catalogs"] += 1
                        doc_stats["files"] += res_doc.get("files", 0)
                        doc_stats["chunks"] += res_doc.get("chunks", 0)
                    has_docs = doc_stats["files"] > 0 or doc_stats["chunks"] > 0
                    if not is_json and verbose and has_docs:
                        print(
                            f"Synchronized {doc_stats['files']} doc files "
                            f"({doc_stats['chunks']} chunks) "
                            f"across {doc_stats['catalogs']} catalog(s)."
                        )
            except Exception:
                pass

        result: dict[str, Any] = {
            "status": "ok",
            "mode": "workspace",
            "embedded_count": embedded_count,
            "docs": doc_stats,
            "doc_stats": doc_stats,
            "telemetry": telemetry,
        }
        if embeddings_error:
            result["embeddings_error"] = embeddings_error
        return result
