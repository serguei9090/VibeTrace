import hashlib
import os
import sys
from pathlib import Path

import pathspec

from vibetrace.indexer.db import IndexerDB
from vibetrace.indexer.git_utils import get_git_delta, get_git_sha
from vibetrace.indexer.parser import ParserFactory
from vibetrace.indexer.resolver import ModuleResolver

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
        nodes, edges = parser.extract(rel_path, source_code)

        # Atomically clear and persist in a single transaction
        db.batch_save_file_index(rel_path, content_hash, nodes, edges)

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
) -> None:
    """Reindex a file or directory recursively."""
    from vibetrace.config import load_config

    target_path = Path(target_path_str).resolve()
    eff_root = (
        project_root.resolve()
        if project_root
        else (target_path if target_path.is_dir() else target_path.parent)
    )

    failed: list[str] = []
    cfg = load_config(eff_root)
    spec = load_ignore_spec(eff_root, cfg.indexer.exclude_patterns)
    resolver = ModuleResolver(eff_root) if eff_root.is_dir() else None

    if not target_path.exists():
        if verbose:
            print(f"Path not found: {target_path}")
        return

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
            if resolver:
                try:
                    rel_p = str(target_path.relative_to(eff_root)).replace("\\", "/")
                except ValueError:
                    rel_p = str(target_path).replace("\\", "/")
                resolver.add_file(rel_p)
            db.update_centrality_scores()
    elif target_path.is_dir():
        target_rel = ""
        try:
            target_rel = str(target_path.relative_to(eff_root)).replace("\\", "/")
        except ValueError:
            pass

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
                    if resolver:
                        resolver.add_file(c)
            if indexed_any or len(deleted) > 0:
                db.update_centrality_scores()
            # Only advance the sync cursor when every file persisted; otherwise retry next run.
            if current_sha and target_rel in ("", ".") and not failed:
                db.set_meta("last_indexed_git_sha", current_sha)
        else:
            # Full walk fallback (initial index, --force, or non-git repo)
            seen_rel_paths: set[str] = set()
            for root, dirs, files in os.walk(target_path):
                current_root = Path(root)

                def _rel(p: Path) -> str:
                    try:
                        return str(p.relative_to(eff_root)).replace("\\", "/")
                    except ValueError:
                        return str(p).replace("\\", "/")

                # 1. Prune standard noisy and gitignored directories (optimization)
                dirs[:] = [
                    d for d in dirs if not is_ignored_path(_rel(current_root / d), spec, True)
                ]

                # 2. Filter files using the shared ignore rule
                for file in files:
                    full_path = current_root / file
                    rel_path = _rel(full_path)
                    if is_ignored_path(rel_path, spec):
                        continue

                    seen_rel_paths.add(rel_path)
                    index_file(
                        db,
                        str(full_path),
                        project_root=eff_root,
                        resolver=resolver,
                        verbose=verbose,
                        force=force,
                        failed=failed,
                    )

            # Reconcile stale files: purge any file recorded in indexed_files
            # that no longer exists on disk
            for indexed_file in db.get_all_indexed_file_paths():
                if target_rel not in ("", ".") and not (
                    indexed_file == target_rel or indexed_file.startswith(f"{target_rel}/")
                ):
                    continue
                if indexed_file not in seen_rel_paths:
                    if verbose:
                        print(f"Purging deleted/stale file from index: {indexed_file}")
                    db.clear_file(indexed_file)
                    if resolver:
                        resolver.remove_file(indexed_file)

            db.update_centrality_scores()
            if current_sha and target_rel in ("", ".") and not failed:
                db.set_meta("last_indexed_git_sha", current_sha)
            elif not current_sha and target_rel in ("", ".") and not failed:
                db.set_meta("last_indexed_git_sha", "non-git")

    if use_lsp and eff_root.is_dir():
        from vibetrace.indexer.lsp_bridge import (
            LSPClient,
            get_default_lsp_command,
            resolve_unresolved_edges,
        )

        cmd = get_default_lsp_command(eff_root)
        if cmd:
            if verbose:
                print(f"Connecting to LSP hybrid bridge ({cmd[0]})...")
            client = LSPClient(cmd, eff_root)
            if client.start():
                try:
                    rewired = resolve_unresolved_edges(db, eff_root, client, verbose=verbose)
                    if rewired > 0:
                        db.update_centrality_scores()
                finally:
                    client.stop()
            elif verbose:
                print(f"Notice: Failed to initialize LSP session with {cmd[0]}.")
        elif verbose:
            print("Notice: No supported LSP server found in PATH.")


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
