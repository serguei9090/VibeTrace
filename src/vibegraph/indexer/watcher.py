import os
import threading
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
        debounce_seconds: float = 0.3,
    ):
        self.db = db
        self.on_change = on_change
        self.root_path = Path(root_path).resolve()
        self.resolver = ModuleResolver(self.root_path)
        self.gitignore_spec = self._load_gitignore()
        self.debounce_seconds = debounce_seconds
        self._debounce_timer: threading.Timer | None = None
        self._lock = threading.Lock()

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

        # Ignore editor swap / temporary files
        fname = path.name
        if fname.startswith((".", "#")) or fname.endswith(("~", ".tmp", ".swp", ".bak")):
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

    def _schedule_post_sync(self) -> None:
        """Debounce centrality recalculation and WebSocket broadcast across burst changes."""
        with self._lock:
            if self._debounce_timer is not None:
                self._debounce_timer.cancel()

            def _run():
                try:
                    self.db.update_centrality_scores()
                except Exception as e:
                    print(f"Error updating centrality scores: {e}")
                self._notify()

            self._debounce_timer = threading.Timer(self.debounce_seconds, _run)
            self._debounce_timer.daemon = True
            self._debounce_timer.start()

    def on_modified(self, event):
        src_path = str(event.src_path)
        if event.is_directory or self._should_ignore(src_path):
            return
        rel_path = self._to_rel_path(src_path)
        print(f"File modified: {rel_path}")
        index_file(
            self.db,
            src_path,
            project_root=self.root_path,
            resolver=self.resolver,
            force=True,
        )
        self._schedule_post_sync()

    def on_created(self, event):
        src_path = str(event.src_path)
        if event.is_directory or self._should_ignore(src_path):
            return
        rel_path = self._to_rel_path(src_path)
        print(f"File created: {rel_path}")
        self.resolver.add_file(rel_path)
        index_file(
            self.db,
            src_path,
            project_root=self.root_path,
            resolver=self.resolver,
            force=True,
        )
        self._schedule_post_sync()

    def on_deleted(self, event):
        src_path = str(event.src_path)
        if self._should_ignore(src_path):
            return

        if event.is_directory:
            rel_dir = self._to_rel_path(src_path)
            prefix = f"{rel_dir}/" if rel_dir not in ("", ".") else ""
            all_files = self.db.get_all_indexed_file_paths()
            deleted_any = False
            for f in all_files:
                if f.startswith(prefix) or f == rel_dir:
                    self.db.clear_file(f)
                    self.resolver.remove_file(f)
                    deleted_any = True
            if deleted_any:
                print(f"Directory deleted: {rel_dir} (cleared indexed files)")
                self._schedule_post_sync()
            return

        rel_path = self._to_rel_path(src_path)
        print(f"File deleted: {rel_path}")
        self.resolver.remove_file(rel_path)
        self.db.clear_file(rel_path)
        self._schedule_post_sync()

    def on_moved(self, event):
        src_path = str(event.src_path)
        dest_path = str(event.dest_path)
        rel_src = self._to_rel_path(src_path)
        rel_dest = self._to_rel_path(dest_path)

        if event.is_directory:
            prefix = f"{rel_src}/" if rel_src not in ("", ".") else ""
            all_files = self.db.get_all_indexed_file_paths()
            for f in all_files:
                if f.startswith(prefix) or f == rel_src:
                    self.db.clear_file(f)
                    self.resolver.remove_file(f)

            if not self._should_ignore(dest_path):
                dest_dir = Path(dest_path)
                if dest_dir.exists():
                    for root, dirs, files in os.walk(dest_dir):
                        dirs[:] = [d for d in dirs if not self._should_ignore(str(Path(root) / d))]
                        for file in files:
                            full_file = Path(root) / file
                            if not self._should_ignore(str(full_file)):
                                rel_f = self._to_rel_path(str(full_file))
                                self.resolver.add_file(rel_f)
                                index_file(
                                    self.db,
                                    str(full_file),
                                    project_root=self.root_path,
                                    resolver=self.resolver,
                                    force=True,
                                )
            print(f"Directory moved: {rel_src} -> {rel_dest}")
            self._schedule_post_sync()
            return

        print(f"File moved: {rel_src} -> {rel_dest}")
        self.resolver.remove_file(rel_src)
        self.db.clear_file(rel_src)
        if not self._should_ignore(dest_path):
            self.resolver.add_file(rel_dest)
            index_file(
                self.db,
                dest_path,
                project_root=self.root_path,
                resolver=self.resolver,
                force=True,
            )
        self._schedule_post_sync()


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
