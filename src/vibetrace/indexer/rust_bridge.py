"""Native Rust FFI bridge (`vibetrace._rust_core`) for GIL-free parallel workspace scanning."""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from vibetrace.indexer.db import Edge, IndexerDB, Node
from vibetrace.indexer.parsers import ParserFactory
from vibetrace.indexer.resolver import ModuleResolver

logger = logging.getLogger(__name__)

_rust_core: Any = None
try:
    from vibetrace import _rust_core as _rc  # type: ignore[attr-defined]

    _rust_core = _rc
    _HAS_RUST_CORE = True
except ImportError:
    try:
        import _rust_core as _rc2  # type: ignore[no-redef]

        _rust_core = _rc2
        _HAS_RUST_CORE = True
    except ImportError:
        _HAS_RUST_CORE = False


def is_native_available() -> bool:
    """Return True if the compiled PyO3 Rust extension (`_rust_core`) is loaded."""
    return _HAS_RUST_CORE and _rust_core is not None


def get_native_version() -> str | None:
    """Return the version string of the compiled Rust extension, or None if unavailable."""
    if not is_native_available():
        return None
    try:
        return str(_rust_core.rust_engine_version())
    except Exception:
        return None


def scan_workspace_native(
    target_path: Path,
    project_root: Path,
    known_hashes: dict[str, str],
    extra_excludes: list[str] | None = None,
    force: bool = False,
) -> Any | None:
    """Invoke the parallel Rust directory walker (`ignore` + `rayon` + `sha2`) releasing the GIL."""
    if not is_native_available():
        return None
    try:
        return _rust_core.py_scan_and_hash_workspace(
            str(target_path.resolve()),
            str(project_root.resolve()),
            known_hashes,
            extra_excludes or [],
            force,
        )
    except Exception as e:
        logger.warning("Native workspace scan failed, falling back: %s", e)
        return None


def extract_batch_native(deltas: list[Any]) -> list[Any]:
    """Invoke parallel Rust Tree-sitter AST extraction across dirty files."""
    if not is_native_available() or not deltas:
        return []
    try:
        return list(_rust_core.py_extract_batch_parallel(deltas))
    except Exception as e:
        logger.warning("Native batch extraction failed: %s", e)
        return []


def reindex_workspace_hybrid(
    db: IndexerDB,
    target_path: Path,
    project_root: Path,
    resolver: ModuleResolver | None = None,
    extra_excludes: list[str] | None = None,
    force: bool = False,
    verbose: bool = True,
    failed: list[str] | None = None,
) -> list[str]:
    """Execute full workspace scan, incremental SHA-256 diff, and single-tx SQLite commit.

    Uses the native Rust `ignore` + `sha2` parallel scanner to eliminate GIL-bound disk I/O and
    hashing, then extracts rich semantic AST nodes/edges (routes, taint contracts, imports)
    for dirty files and commits the entire workspace delta in a SINGLE SQLite WAL transaction.
    Returns list of processed relative file paths.
    """
    scan_res = scan_workspace_native(
        target_path=target_path,
        project_root=project_root,
        known_hashes=db.get_all_file_hashes(),
        extra_excludes=extra_excludes,
        force=force,
    )
    if scan_res is None:
        raise RuntimeError(
            "Native Rust core (`vibetrace._rust_core`) is required. "
            "Build it with: cargo build --release -p vibetrace-core --features extension-module"
        )

    dirty_deltas = list(scan_res.dirty_files)
    deleted_files = list(scan_res.deleted_files)

    if verbose and deleted_files:
        for del_f in deleted_files:
            print(f"Purging deleted/stale file from index: {del_f}")
            if resolver:
                resolver.remove_file(del_f)

    file_records: list[
        tuple[
            str,
            str,
            list[Node],
            list[Edge],
            dict[str, tuple[str, str]] | None,
            dict[str, tuple[str, str]] | None,
        ]
    ] = []
    processed_paths: list[str] = []

    total_dirty = len(dirty_deltas)
    for idx, delta in enumerate(dirty_deltas, 1):
        rel_path: str = delta.rel_path
        content_hash: str = delta.content_hash
        source_code: bytes = bytes(delta.source_bytes)

        try:
            parser = ParserFactory.get_parser(rel_path, resolver=resolver)
            if not parser:
                continue
            pct = (idx / total_dirty) * 100.0
            if verbose:
                print(f"[{idx}/{total_dirty} {pct:5.1f}%] Indexing {rel_path}...")
            parser.last_imported_symbols = {}
            parser.last_target_symbols = {}
            nodes, edges = parser.extract(rel_path, source_code)

            imported = dict(getattr(parser, "last_imported_symbols", {}) or {})
            targets = dict(getattr(parser, "last_target_symbols", {}) or {})
            file_records.append((rel_path, content_hash, nodes, edges, imported, targets))
            processed_paths.append(rel_path)
            if resolver:
                resolver.add_file(rel_path)
            if verbose:
                print(f"  -> Extracted {len(nodes)} nodes, {len(edges)} edges.")
        except Exception as e:
            if failed is not None:
                failed.append(rel_path)
            if verbose:
                print(f"Error indexing {rel_path}: {e}")

    if file_records or deleted_files:
        db.batch_save_workspace_index(file_records, deleted_files=deleted_files)

    return processed_paths
