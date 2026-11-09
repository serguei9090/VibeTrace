import hashlib
import sys
import time
from pathlib import Path
from typing import Any

import pathspec

from vibetrace.indexer.db import IndexerDB
from vibetrace.indexer.git_utils import get_git_delta, get_git_sha
from vibetrace.indexer.parsers import ParserFactory
from vibetrace.indexer.resolver import ModuleResolver
from vibetrace.indexer.rust_bridge import reindex_workspace_hybrid

SKIP_DIRS = frozenset(
    {
        ".git",
        ".venv",
        ".vibetrace",
        "node_modules",
        "__pycache__",
        ".ruff_cache",
        "dist",
        "build",
        "vibetrace_context",
        "vibegraph_context",
    }
)


def is_ignored_path(rel_path: str, spec: pathspec.PathSpec | None, is_dir: bool = False) -> bool:
    """Single ignore rule shared by full walk, git delta, and watcher (project-relative path)."""
    rel = rel_path.replace("\\", "/").strip("/")
    parts = rel.split("/") if rel else []
    dir_parts = parts if is_dir else parts[:-1]
    if any(p in SKIP_DIRS or p.startswith(".") for p in dir_parts):
        return True
    if spec is None or not rel:
        return False
    return spec.match_file(rel) or (is_dir and spec.match_file(f"{rel}/"))


def index_file(
    db: IndexerDB,
    file_path: str,
    project_root: Path | None = None,
    resolver: ModuleResolver | None = None,
    verbose: bool = True,
    force: bool = False,
    failed: list[str] | None = None,
) -> bool:
    """Index a single file. Returns True if indexed, False if skipped or failed.

    Errors are appended to `failed` (when given) so callers can avoid committing sync state.
    """
    try:
        abs_path = Path(file_path).resolve()

        # Calculate relative path
        if project_root:
            try:
                rel_path = str(abs_path.relative_to(project_root.resolve())).replace("\\", "/")
            except ValueError:
                rel_path = str(abs_path).replace("\\", "/")
        else:
            try:
                rel_path = str(abs_path.relative_to(Path.cwd().resolve())).replace("\\", "/")
            except ValueError:
                rel_path = str(abs_path).replace("\\", "/")

        parser = ParserFactory.get_parser(rel_path, resolver=resolver)
        if not parser:
            if verbose:
                print(f"Skipping {file_path} (unsupported language)")
            return False

        # Read using abs_path
        with open(abs_path, "rb") as f:
            source_code = f.read()

        content_hash = hashlib.sha256(source_code).hexdigest()
        if not force and db.get_file_hash(rel_path) == content_hash:
            if verbose:
                print(f"Skipping {rel_path} (unchanged)")
            return False

        if verbose:
            print(f"Indexing {rel_path}...")
        parser.last_imported_symbols = {}
        parser.last_target_symbols = {}
        nodes, edges = parser.extract(rel_path, source_code)

        # Atomically clear and persist in a single transaction
        db.batch_save_file_index(
            rel_path,
            content_hash,
            nodes,
            edges,
            imported_symbols=getattr(parser, "last_imported_symbols", None),
            target_symbols=getattr(parser, "last_target_symbols", None),
        )

        if verbose:
            print(f"  -> Extracted {len(nodes)} nodes, {len(edges)} edges.")
        return True

    except Exception as e:
        if failed is not None:
            failed.append(file_path)
        if verbose:
            print(f"Error indexing {file_path}: {e}")
        return False


def load_ignore_spec(
    root_path: Path,
    extra_patterns: list[str] | None = None,
) -> pathspec.PathSpec | None:
    """Load .gitignore combined with configured exclude patterns."""
    lines: list[str] = []
    gitignore_path = root_path / ".gitignore"
    if gitignore_path.exists():
        try:
            with open(gitignore_path, encoding="utf-8") as f:
                lines.extend(f.readlines())
        except Exception as e:
            print(f"Warning: Failed to load .gitignore: {e}")

    if extra_patterns:
        lines.extend(extra_patterns)

    if lines:
        return pathspec.PathSpec.from_lines("gitignore", lines)
    return None


def load_gitignore(root_path: Path) -> pathspec.PathSpec | None:
    """Load .gitignore combined with configured exclude patterns from the root path."""
    from vibetrace.config import load_config

    cfg = load_config(root_path)
    return load_ignore_spec(root_path, cfg.indexer.exclude_patterns)


def reindex_all(
    db: IndexerDB,
    target_path_str: str,
    verbose: bool = True,
    force: bool = False,
    project_root: Path | None = None,
    use_lsp: bool = False,
) -> dict[str, Any]:
    """Reindex a file or directory recursively.

    Returns telemetry dict containing:
    files_processed, elapsed_seconds, throughput_fps, and modified_files.
    """
    t_start = time.perf_counter()
    from vibetrace.config import load_config

    target_path = Path(target_path_str).resolve()
    if project_root:
        eff_root = project_root.resolve()
    elif target_path.is_dir():
        eff_root = target_path
    else:
        eff_root = target_path.parent

    failed: list[str] = []
    processed_files: list[str] = []
    cfg = load_config(eff_root)
    spec = load_ignore_spec(eff_root, cfg.indexer.exclude_patterns)
    resolver = ModuleResolver(eff_root) if eff_root.is_dir() else None

    if not target_path.exists():
        if verbose:
            print(f"Path not found: {target_path}")
        return {
            "files_processed": 0,
            "elapsed_seconds": 0.0,
            "throughput_fps": 0.0,
            "modified_files": [],
        }

    if target_path.is_file():
        success = index_file(
            db,
            str(target_path),
            project_root=eff_root,
            resolver=resolver,
            verbose=verbose,
            force=force,
            failed=failed,
        )
        if success:
            try:
                rel_p = str(target_path.relative_to(eff_root)).replace("\\", "/")
            except ValueError:
                rel_p = str(target_path).replace("\\", "/")
            processed_files.append(rel_p)
            if resolver:
                resolver.add_file(rel_p)
            db.link_cross_file_edges()
            db.update_centrality_scores()
    elif target_path.is_dir():
        target_rel = ""
        try:
            target_rel = str(target_path.relative_to(eff_root)).replace("\\", "/")
        except ValueError:
            target_rel = str(target_path).replace("\\", "/")

        current_sha = get_git_sha(eff_root)
        last_sha = db.get_meta("last_indexed_git_sha") if not force else None
        delta = get_git_delta(eff_root, last_sha) if (last_sha and not force) else None

        if delta is not None:
            changed, deleted = delta
            if verbose:
                print(
                    f"Git delta cache: {len(changed)} changed/added, {len(deleted)} deleted files."
                )
            indexed_any = False
            for d in deleted:
                if target_rel not in ("", ".") and not (
                    d == target_rel or d.startswith(f"{target_rel}/")
                ):
                    continue
                db.clear_file(d)
                if resolver:
                    resolver.remove_file(d)
                indexed_any = True
            for c in changed:
                if target_rel not in ("", ".") and not (
                    c == target_rel or c.startswith(f"{target_rel}/")
                ):
                    continue
                full_path = eff_root / c
                if is_ignored_path(c, spec):
                    db.clear_file(c)
                    if resolver:
                        resolver.remove_file(c)
                    continue
                if index_file(
                    db,
                    str(full_path),
                    project_root=eff_root,
                    resolver=resolver,
                    verbose=verbose,
                    force=force,
                    failed=failed,
                ):
                    indexed_any = True
                    processed_files.append(c)
                    if resolver:
                        resolver.add_file(c)
            if indexed_any or len(deleted) > 0:
                db.link_cross_file_edges()
                db.update_centrality_scores()
            # Only advance the sync cursor when every file persisted; otherwise retry next run.
            if current_sha and target_rel in ("", ".") and not failed:
                db.set_meta("last_indexed_git_sha", current_sha)
        else:
            # Native Hybrid Rust workspace scan, SHA-256 diff, and single-tx SQLite commit
            hybrid_processed = reindex_workspace_hybrid(
                db=db,
                target_path=target_path,
                project_root=eff_root,
                resolver=resolver,
                extra_excludes=cfg.indexer.exclude_patterns,
                force=force,
                verbose=verbose,
                failed=failed,
            )
            processed_files.extend(hybrid_processed)
            db.link_cross_file_edges()
            db.update_centrality_scores()
            if current_sha and target_rel in ("", ".") and not failed:
                db.set_meta("last_indexed_git_sha", current_sha)
            elif not current_sha and target_rel in ("", ".") and not failed:
                db.set_meta("last_indexed_git_sha", "non-git")

    if use_lsp and eff_root.is_dir():
        from vibetrace.indexer.lsp_bridge import (
            MultiLanguageLSPManager,
            resolve_unresolved_edges_polyglot,
        )

        try:
            rewired = resolve_unresolved_edges_polyglot(
                db=db,
                project_root=eff_root,
                verbose=verbose,
                modified_files=processed_files if processed_files else None,
            )
            if rewired > 0:
                db.update_centrality_scores()
        finally:
            MultiLanguageLSPManager.stop_for_project(eff_root)
    elif not use_lsp and verbose and eff_root.is_dir():
        # Telemetry suggestion if polyglot source code exists but LSP wasn't enabled
        from vibetrace.indexer.lsp_bridge import get_default_lsp_command

        if not get_default_lsp_command(eff_root):
            # Check if repo has source code extensions that would benefit from LSP
            has_lsp_candidates = any(
                p.endswith((".py", ".ts", ".tsx", ".js", ".jsx", ".go", ".rs"))
                for p in processed_files
            )
            if has_lsp_candidates:
                print("💡 Tip: Run 'vibetrace tools install-lsp' for deep type resolution.")

    elapsed = max(time.perf_counter() - t_start, 0.0001)
    file_count = len(processed_files)
    throughput = file_count / elapsed

    if verbose and file_count > 0:
        counts = db.get_statistics()
        node_c = counts.get("nodes", 0)
        edge_c = counts.get("edges", 0)
        try:
            print(
                f"[OK] Indexed {file_count} files in {elapsed:.2f}s "
                f"({throughput:.1f} files/sec) [rust-native] * {node_c} nodes, {edge_c} edges."
            )
        except UnicodeEncodeError:
            pass

    return {
        "files_processed": file_count,
        "elapsed_seconds": round(elapsed, 4),
        "throughput_fps": round(throughput, 1),
        "modified_files": processed_files,
        "engine": "rust-native",
        "edge_resolution": db.get_edge_resolution_metrics(),
    }


def main() -> None:
    if len(sys.argv) < 2:
        print("Usage: python -m vibetrace.indexer.main <directory_or_file>")
        sys.exit(1)

    from vibetrace.services.graph_service import get_context_for_path

    target = sys.argv[1]
    db, root = get_context_for_path(target, auto_sync=False)
    reindex_all(db, target, project_root=root)


if __name__ == "__main__":
    main()
