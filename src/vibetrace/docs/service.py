"""Documentation RAG catalog management, indexing, and hybrid retrieval service."""

from __future__ import annotations

import os
import tomllib
from pathlib import Path
from typing import Any, Literal

from vibetrace.docs.chunker import HeadingChunk, chunk_markdown, chunk_pdf, chunk_text
from vibetrace.docs.db import DocDB, get_global_doc_db, get_local_doc_db
from vibetrace.docs.schemas import DocCatalogInfo, DocChunkItem
from vibetrace.indexer.embeddings import EmbeddingEngine


def get_global_config_path() -> Path:
    """Return path to user-global VibeTrace config (~/.vibetrace/config.toml)."""
    env_global = os.environ.get("VIBETRACE_GLOBAL_ROOT")
    p = (Path(env_global) if env_global else Path.home() / ".vibetrace") / "config.toml"
    p.parent.mkdir(parents=True, exist_ok=True)
    return p


def get_project_config_path(project_root: Path) -> Path:
    """Return path to project .vibetrace.toml."""
    return project_root / ".vibetrace.toml"


def _read_catalogs_from_toml(path: Path) -> dict[str, list[str]]:
    """Parse [docs.catalogs] table of lists from a TOML file."""
    if not path.is_file():
        return {}
    try:
        with path.open("rb") as f:
            data = tomllib.load(f)
        docs_sec = data.get("docs", {})
        catalogs = docs_sec.get("catalogs", {})
        res: dict[str, list[str]] = {}
        for k, v in catalogs.items():
            if isinstance(v, list):
                res[k] = [str(p) for p in v]
            elif isinstance(v, str):
                res[k] = [v]
        return res
    except Exception:
        return {}


def _write_catalogs_to_toml(path: Path, catalogs: dict[str, list[str]]) -> None:
    """Persist [docs.catalogs] table into TOML file while preserving other sections."""
    lines: list[str] = []
    if path.is_file():
        try:
            content = path.read_text(encoding="utf-8")
            lines = content.splitlines()
        except Exception:
            lines = []

    # Strip existing [docs.catalogs] section
    filtered_lines: list[str] = []
    in_docs_catalogs = False
    for line in lines:
        stripped = line.strip()
        if stripped.startswith("[") and stripped.endswith("]"):
            if stripped == "[docs.catalogs]":
                in_docs_catalogs = True
                continue
            else:
                in_docs_catalogs = False
        if not in_docs_catalogs:
            filtered_lines.append(line)

    # Append new [docs.catalogs]
    if filtered_lines and filtered_lines[-1].strip():
        filtered_lines.append("")
    filtered_lines.append("[docs.catalogs]")
    for cat_name, paths in sorted(catalogs.items()):
        norm_paths = [p.replace("\\", "/") for p in paths]
        paths_json = ", ".join(f'"{p}"' for p in norm_paths)
        filtered_lines.append(f"{cat_name} = [{paths_json}]")

    path.write_text("\n".join(filtered_lines) + "\n", encoding="utf-8")


class DocCatalogManager:
    """Manages local and global documentation catalogs."""

    @staticmethod
    def list_catalogs(project_root: Path | None = None) -> list[DocCatalogInfo]:
        """List all active documentation catalogs with local precedence."""
        global_cats = _read_catalogs_from_toml(get_global_config_path())
        local_cats: dict[str, list[str]] = {}
        if project_root:
            local_cats = _read_catalogs_from_toml(get_project_config_path(project_root))

        infos: list[DocCatalogInfo] = []
        global_db = get_global_doc_db()
        local_db = get_local_doc_db(project_root) if project_root else None

        g_summary = {s["catalog"]: s for s in global_db.get_catalogs_summary()}
        l_summary = {s["catalog"]: s for s in local_db.get_catalogs_summary()} if local_db else {}

        # 1. Local catalogs first (precedence)
        for name, paths in local_cats.items():
            stats = l_summary.get(name, {})
            infos.append(
                DocCatalogInfo(
                    name=name,
                    scope="local",
                    paths=paths,
                    doc_count=stats.get("doc_count", 0),
                    chunk_count=stats.get("chunk_count", 0),
                )
            )

        # 2. Global catalogs (only if not overridden locally)
        for name, paths in global_cats.items():
            if name in local_cats:
                continue
            stats = g_summary.get(name, {})
            infos.append(
                DocCatalogInfo(
                    name=name,
                    scope="global",
                    paths=paths,
                    doc_count=stats.get("doc_count", 0),
                    chunk_count=stats.get("chunk_count", 0),
                )
            )

        return infos

    @staticmethod
    def add_path(
        catalog_name: str,
        path_str: str,
        global_scope: bool = False,
        project_root: Path | None = None,
    ) -> None:
        """Add a directory path to a local or global documentation catalog."""
        cfg_file = (
            get_global_config_path()
            if global_scope
            else get_project_config_path(project_root or Path.cwd())
        )
        current = _read_catalogs_from_toml(cfg_file)
        norm_p = path_str.replace("\\", "/")
        if catalog_name not in current:
            current[catalog_name] = [norm_p]
        elif norm_p not in current[catalog_name]:
            current[catalog_name].append(norm_p)
        _write_catalogs_to_toml(cfg_file, current)

    @staticmethod
    def remove_catalog(
        catalog_name: str,
        global_scope: bool = False,
        project_root: Path | None = None,
    ) -> bool:
        """Remove a catalog from local or global config."""
        cfg_file = (
            get_global_config_path()
            if global_scope
            else get_project_config_path(project_root or Path.cwd())
        )
        current = _read_catalogs_from_toml(cfg_file)
        if catalog_name in current:
            del current[catalog_name]
            _write_catalogs_to_toml(cfg_file, current)
            return True
        return False


class DocIndexService:
    """Indexes files in documentation catalogs into SQLite FTS5 and vector tables."""

    SUPPORTED_DOC_EXTS = (".md", ".markdown", ".txt", ".pdf")

    @classmethod
    def index_catalog(
        cls,
        catalog_name: str,
        paths: list[str],
        db: DocDB,
        base_dir: Path,
        force: bool = False,
    ) -> dict[str, int]:
        """Index all documentation files within the given paths for a catalog."""
        indexed_files = 0
        total_chunks = 0
        engine = EmbeddingEngine(project_root=base_dir)
        has_embed = engine.is_available()

        for p_str in paths:
            target_p = Path(p_str)
            if not target_p.is_absolute():
                target_p = base_dir / target_p
            if not target_p.exists():
                continue

            files_to_index: list[Path] = []
            if target_p.is_file():
                if target_p.suffix.lower() in cls.SUPPORTED_DOC_EXTS:
                    files_to_index.append(target_p)
            else:
                for root, _, files in os.walk(target_p):
                    for f in files:
                        p_file = Path(root) / f
                        if p_file.suffix.lower() in cls.SUPPORTED_DOC_EXTS:
                            files_to_index.append(p_file)

            for fpath in files_to_index:
                rel_file = (
                    str(fpath.relative_to(base_dir)).replace("\\", "/")
                    if fpath.is_relative_to(base_dir)
                    else str(fpath).replace("\\", "/")
                )
                mtime = fpath.stat().st_mtime
                prev_mtime = db.get_file_mtime(catalog_name, rel_file)

                if not force and prev_mtime is not None and abs(prev_mtime - mtime) < 1e-4:
                    continue

                chunks = cls._extract_chunks(fpath)
                if not chunks:
                    db.delete_file_chunks(catalog_name, rel_file)
                    continue

                embeddings: list[list[float]] | None = None
                if has_embed:
                    texts = [f"{c.heading_path}\n{c.content}" for c in chunks]
                    try:
                        embeddings = engine.embed_texts(texts)
                    except Exception:
                        embeddings = None

                db.upsert_chunks(catalog_name, rel_file, mtime, chunks, embeddings)
                indexed_files += 1
                total_chunks += len(chunks)

        return {"files": indexed_files, "chunks": total_chunks}

    @classmethod
    def _extract_chunks(cls, fpath: Path) -> list[HeadingChunk]:
        ext = fpath.suffix.lower()
        if ext in (".md", ".markdown"):
            content = fpath.read_text(encoding="utf-8", errors="replace")
            return chunk_markdown(content)
        elif ext == ".pdf":
            return chunk_pdf(fpath)
        else:
            content = fpath.read_text(encoding="utf-8", errors="replace")
            return chunk_text(content)


class DocSearchService:
    """Executes 2-stream hybrid RRF search across local and global documentation stores."""

    @classmethod
    def search(
        cls,
        query: str,
        catalog: str | None = None,
        scope: Literal["all", "local", "global"] = "all",
        project_root: Path | None = None,
        limit: int = 5,
    ) -> list[DocChunkItem]:
        """Search documentation using hybrid reciprocal rank fusion."""
        targets: list[tuple[DocDB, Literal["local", "global"]]] = []
        if scope in ("local", "all") and project_root:
            targets.append((get_local_doc_db(project_root), "local"))
        if scope in ("global", "all"):
            targets.append((get_global_doc_db(), "global"))

        engine = EmbeddingEngine(project_root=project_root) if project_root else None
        q_vec: list[float] | None = None
        if engine and engine.is_available():
            try:
                emb_res = engine.embed_texts([query])
                if emb_res:
                    q_vec = emb_res[0]
            except Exception:
                q_vec = None

        bm25_hits: list[tuple[DocDB, Literal["local", "global"], dict[str, Any]]] = []
        vec_hits: list[tuple[DocDB, Literal["local", "global"], dict[str, Any]]] = []

        for db, sc in targets:
            b_res = db.search_bm25(query, catalog=catalog, limit=limit * 2)
            for r in b_res:
                bm25_hits.append((db, sc, r))

            if q_vec:
                v_res = db.search_vector(q_vec, catalog=catalog, limit=limit * 2)
                for r in v_res:
                    vec_hits.append((db, sc, r))

        # Reciprocal Rank Fusion (k=60)
        rrf_scores: dict[str, float] = {}
        chunk_map: dict[str, tuple[Literal["local", "global"], dict[str, Any]]] = {}
        sources_map: dict[str, set[str]] = {}

        for rank, (_, sc, item) in enumerate(bm25_hits, start=1):
            cid = f"{sc}:{item['id']}"
            rrf_scores[cid] = rrf_scores.get(cid, 0.0) + (1.0 / (60.0 + rank))
            chunk_map[cid] = (sc, item)
            sources_map.setdefault(cid, set()).add("bm25")

        for rank, (_, sc, item) in enumerate(vec_hits, start=1):
            cid = f"{sc}:{item['id']}"
            rrf_scores[cid] = rrf_scores.get(cid, 0.0) + (1.0 / (60.0 + rank))
            chunk_map[cid] = (sc, item)
            sources_map.setdefault(cid, set()).add("vector")

        sorted_items = sorted(rrf_scores.items(), key=lambda x: x[1], reverse=True)
        results: list[DocChunkItem] = []
        for cid, score in sorted_items[:limit]:
            sc, item = chunk_map[cid]
            results.append(
                DocChunkItem(
                    id=item["id"],
                    catalog=item["catalog"],
                    scope=sc,
                    file_path=item["file_path"],
                    heading_path=item["heading_path"],
                    start_line=item["start_line"],
                    end_line=item["end_line"],
                    content=item["content"],
                    score=round(score, 6),
                    match_sources=sorted(sources_map.get(cid, set())),
                )
            )
        return results
