import hashlib
import os
import sys
import time
from pathlib import Path

import pathspec

from vibegraph.indexer.db import IndexerDB
from vibegraph.indexer.parser import ParserFactory
from vibegraph.indexer.resolver import ModuleResolver


def index_file(
    db: IndexerDB,
    file_path: str,
    project_root: Path | None = None,
    resolver: ModuleResolver | None = None,
    verbose: bool = True,
    force: bool = False,
) -> bool:
    """Index a single file. Returns True if indexed, False if skipped."""
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

        # Clear old data for this file
        db.clear_file(rel_path)

        # Insert new data
        for node in nodes:
            db.upsert_node(node)

        for edge in edges:
            db.upsert_edge(edge)

        db.update_file_hash(rel_path, content_hash, int(time.time()))

        if verbose:
            print(f"  -> Extracted {len(nodes)} nodes, {len(edges)} edges.")
        return True

    except Exception as e:
        if verbose:
            print(f"Error indexing {file_path}: {e}")
        return False


def load_gitignore(root_path: Path) -> pathspec.PathSpec | None:
    """Load .gitignore from the root path if it exists."""
    gitignore_path = root_path / ".gitignore"
    if gitignore_path.exists():
        try:
            with open(gitignore_path, encoding="utf-8") as f:
                return pathspec.PathSpec.from_lines("gitignore", f)
        except Exception as e:
            print(f"Warning: Failed to load .gitignore: {e}")
    return None


def reindex_all(
    db: IndexerDB,
    target_path_str: str,
    verbose: bool = True,
    force: bool = False,
    project_root: Path | None = None,
) -> None:
    """Reindex a file or directory recursively."""
    target_path = Path(target_path_str).resolve()
    eff_root = (
        project_root.resolve()
        if project_root
        else (target_path if target_path.is_dir() else target_path.parent)
    )

    # default skips
    skip_dirs = {
        ".git",
        ".venv",
        "node_modules",
        "__pycache__",
        ".ruff_cache",
        "dist",
        "build",
        "vibegraph_context",
    }

    spec = load_gitignore(eff_root)
    resolver = ModuleResolver(eff_root) if eff_root.is_dir() else None

    if target_path.is_file():
        index_file(
            db,
            str(target_path),
            project_root=eff_root,
            resolver=resolver,
            verbose=verbose,
            force=force,
        )
    elif target_path.is_dir():
        for root, dirs, files in os.walk(target_path):
            current_root = Path(root)

            # 1. Prune standard noisy directories first (optimization)
            dirs[:] = [d for d in dirs if d not in skip_dirs and not d.startswith(".")]

            # 2. Filter files using gitignore
            for file in files:
                full_path = current_root / file

                # Check gitignore if available
                if spec:
                    try:
                        rel_path = full_path.relative_to(eff_root)
                        if spec.match_file(str(rel_path)):
                            continue
                    except ValueError:
                        pass

                index_file(
                    db,
                    str(full_path),
                    project_root=eff_root,
                    resolver=resolver,
                    verbose=verbose,
                    force=force,
                )
    else:
        if verbose:
            print(f"Path not found: {target_path}")


def main() -> None:
    if len(sys.argv) < 2:
        print("Usage: python -m vibegraph.indexer.main <directory_or_file>")
        sys.exit(1)

    db = IndexerDB()
    reindex_all(db, sys.argv[1])


if __name__ == "__main__":
    main()
