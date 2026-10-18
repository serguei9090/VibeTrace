import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

from watchdog.events import FileSystemEventHandler
from watchdog.observers import Observer

from vibegraph.indexer.db import IndexerDB
from vibegraph.indexer.main import index_file
from vibegraph.indexer.resolver import ModuleResolver


class CodeChangeHandler(FileSystemEventHandler):
    def __init__(
        self,
        db: IndexerDB,
        on_change: Callable[[], None] | None = None,
        root_path: str = ".",
    ):
        self.db = db
        self.on_change = on_change
        self.root_path = Path(root_path).resolve()
        self.resolver = ModuleResolver(self.root_path)
        self.gitignore_spec = self._load_gitignore()

    def _load_gitignore(self):
        gitignore_path = self.root_path / ".gitignore"
        if gitignore_path.exists():
            try:
                import pathspec

                with open(gitignore_path, encoding="utf-8") as f:
                    return pathspec.PathSpec.from_lines("gitignore", f)
            except Exception as e:
                print(f"Warning: Failed to load .gitignore: {e}")
        return None

    def _to_rel_path(self, src_path: str) -> str:
        try:
            return str(Path(src_path).resolve().relative_to(self.root_path)).replace("\\", "/")
        except ValueError:
            return str(Path(src_path).resolve()).replace("\\", "/")

    def _should_ignore(self, file_path: str) -> bool:
        """Check if file should be ignored based on gitignore and common patterns."""
        path = Path(file_path).resolve()

        # Standard strict ignores
        ignore_patterns = [
            ".git",
            ".venv",
            "node_modules",
            "__pycache__",
            ".ruff_cache",
            "dist",
            "build",
            "vibegraph_context",
        ]
        if any(p in path.parts for p in ignore_patterns):
            return True

        if self.gitignore_spec:
            try:
                rel_path = path.relative_to(self.root_path)
                if self.gitignore_spec.match_file(str(rel_path)):
                    return True
            except ValueError:
                pass  # Not relative to root

        return False

    def _notify(self):
        if self.on_change:
            try:
                self.on_change()
            except Exception as e:
                print(f"Error in watcher callback: {e}")

    def on_modified(self, event):
        src_path = str(event.src_path)
        if event.is_directory or self._should_ignore(src_path):
            return
        rel_path = self._to_rel_path(src_path)
        print(f"File modified: {rel_path}")
        if index_file(
            self.db,
            src_path,
            project_root=self.root_path,
            resolver=self.resolver,
            force=True,
        ):
            self.db.update_centrality_scores()
        self._notify()

    def on_created(self, event):
        src_path = str(event.src_path)
        if event.is_directory or self._should_ignore(src_path):
            return
        rel_path = self._to_rel_path(src_path)
        print(f"File created: {rel_path}")
        self.resolver.add_file(rel_path)
        if index_file(
            self.db,
            src_path,
            project_root=self.root_path,
            resolver=self.resolver,
            force=True,
        ):
            self.db.update_centrality_scores()
        self._notify()

    def on_deleted(self, event):
        src_path = str(event.src_path)
        if event.is_directory or self._should_ignore(src_path):
            return
        rel_path = self._to_rel_path(src_path)
        print(f"File deleted: {rel_path}")
        self.resolver.remove_file(rel_path)
        self.db.clear_file(rel_path)
        self.db.update_centrality_scores()
        self._notify()

    def on_moved(self, event):
        src_path = str(event.src_path)
        dest_path = str(event.dest_path)
        if event.is_directory:
            return
        rel_src = self._to_rel_path(src_path)
        rel_dest = self._to_rel_path(dest_path)
        print(f"File moved: {rel_src} -> {rel_dest}")
        self.resolver.remove_file(rel_src)
        self.db.clear_file(rel_src)
        if not self._should_ignore(dest_path):
            self.resolver.add_file(rel_dest)
            if index_file(
                self.db,
                dest_path,
                project_root=self.root_path,
                resolver=self.resolver,
                force=True,
            ):
                self.db.update_centrality_scores()
        else:
            self.db.update_centrality_scores()
        self._notify()


def start_observer(path: str, db: IndexerDB, callback: Callable[[], None] | None = None) -> Any:
    """Start the observer and return it (non-blocking)."""
    path_obj = Path(path).resolve()
    if not path_obj.exists():
        raise FileNotFoundError(f"Watcher target path does not exist: {path_obj}")

    event_handler = CodeChangeHandler(db, callback, root_path=str(path_obj))
    observer = Observer()
    observer.schedule(event_handler, str(path_obj), recursive=True)
    observer.start()
    print(f"VibeGraph Watcher started on {path_obj}")
    return observer


def start_watcher_blocking(path: str):
    """Blocking version for CLI usage."""
    db = IndexerDB()
    observer = start_observer(path, db)
    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        observer.stop()
    observer.join()


if __name__ == "__main__":
    import sys

    if len(sys.argv) < 2:
        print("Usage: python -m vibegraph.indexer.watcher <directory>")
    else:
        start_watcher_blocking(sys.argv[1])
