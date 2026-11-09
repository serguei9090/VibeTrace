import logging
import os
import threading
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

from watchdog.events import FileSystemEventHandler
from watchdog.observers import Observer

from vibetrace.indexer.db import IndexerDB
from vibetrace.indexer.main import index_file, is_ignored_path, load_gitignore
from vibetrace.indexer.resolver import ModuleResolver

logger = logging.getLogger(__name__)


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
        self.gitignore_spec = load_gitignore(self.root_path)
        self.debounce_seconds = debounce_seconds
        self._debounce_timer: threading.Timer | None = None
        self._lock = threading.Lock()

    def _to_rel_path(self, src_path: str) -> str:
        try:
            return str(Path(src_path).resolve().relative_to(self.root_path)).replace("\\", "/")
        except ValueError:
            return str(Path(src_path).resolve()).replace("\\", "/")

    def _should_ignore(self, file_path: str) -> bool:
        """Check if file should be ignored using the indexer's shared, root-relative rule."""
        path = Path(file_path).resolve()
        try:
            rel_path = str(path.relative_to(self.root_path)).replace("\\", "/")
        except ValueError:
            return True  # Outside the watched project root

        # Ignore SQLite DB/WAL files, editor swap / temporary files
        fname = path.name
        if fname.startswith((".", "#", "vibetrace.db", "vibegraph.db")) or fname.endswith(
            ("~", ".tmp", ".swp", ".bak", ".db-wal", ".db-shm", ".lance", ".manifest")
        ):
            return True

        # Relative parts only: an ancestor of the root named `build`/`dist` must not matter.
        return is_ignored_path(rel_path, self.gitignore_spec, is_dir=path.is_dir())

    def _notify(self):
        if self.on_change:
            try:
                self.on_change()
            except Exception as e:
                logger.error("Error in watcher callback: %s", e)

    def _schedule_post_sync(self) -> None:
        """Debounce centrality recalculation and WebSocket broadcast across burst changes."""
        with self._lock:
            if self._debounce_timer is not None:
                self._debounce_timer.cancel()

            def _run():
                try:
                    from vibetrace.indexer.lsp_bridge import (
                        LSPDaemonManager,
                        resolve_unresolved_edges,
                    )

                    lsp_client = LSPDaemonManager.get_client(self.root_path)
                    if lsp_client:
                        resolve_unresolved_edges(
                            self.db,
                            self.root_path,
                            lsp_client,
                            verbose=False,
                        )
                except Exception as e:
                    logger.debug("LSP background incremental resolution skipped: %s", e)

                try:
                    self.db.update_centrality_scores()
                except Exception as e:
                    print(f"Error updating centrality scores: {e}")
                self._notify()

            self._debounce_timer = threading.Timer(self.debounce_seconds, _run)
            self._debounce_timer.daemon = True
            self._debounce_timer.start()

    def stop(self) -> None:
        """Cancel any pending debounce timer on shutdown and release warm LSP daemon."""
        with self._lock:
            if self._debounce_timer is not None:
                self._debounce_timer.cancel()
                self._debounce_timer = None
        try:
            from vibetrace.indexer.lsp_bridge import LSPDaemonManager

            LSPDaemonManager.stop_client(self.root_path)
        except Exception:
            pass

    def _handle_doc_change(self, src_path: str, is_deleted: bool = False) -> bool:
        """Handle real-time sync for documentation files in local catalogs."""
        try:
            from vibetrace.docs.db import DocDB
            from vibetrace.docs.service import DocCatalogManager, DocIndexService

            p = Path(src_path)
            if p.suffix.lower() not in DocIndexService.SUPPORTED_DOC_EXTS:
                return False

            local_cats = [
                c for c in DocCatalogManager.list_catalogs(self.root_path) if c.scope == "local"
            ]
            for cat in local_cats:
                for cat_p_str in cat.paths:
                    cat_p = self.root_path / cat_p_str
                    if p.is_relative_to(cat_p):
                        doc_db = DocDB(self.db.db_path)
                        rel_file = str(p.relative_to(self.root_path)).replace("\\", "/")
                        if is_deleted:
                            doc_db.delete_file_chunks(cat.name, rel_file)
                            print(f"Doc file deleted: {rel_file}")
                        else:
                            DocIndexService.index_catalog(
                                cat.name, [str(p)], doc_db, self.root_path, force=True
                            )
                            print(f"Doc file synchronized: {rel_file}")
                        return True
        except Exception as e:
            logger.debug("Doc change handling skipped or failed: %s", e)
        return False

    def on_modified(self, event):
        src_path = str(event.src_path)
        if event.is_directory or self._should_ignore(src_path):
            return
        rel_path = self._to_rel_path(src_path)
        indexed = index_file(
            self.db,
            src_path,
            project_root=self.root_path,
            resolver=self.resolver,
            force=False,
        )
        if indexed:
            print(f"File modified: {rel_path}")
            self._schedule_post_sync()
        elif self._handle_doc_change(src_path):
            self._schedule_post_sync()

    def on_created(self, event):
        src_path = str(event.src_path)
        if event.is_directory or self._should_ignore(src_path):
            return
        rel_path = self._to_rel_path(src_path)
        indexed = index_file(
            self.db,
            src_path,
            project_root=self.root_path,
            resolver=self.resolver,
            force=True,
        )
        if indexed:
            print(f"File created: {rel_path}")
            self.resolver.add_file(rel_path)
            self._schedule_post_sync()
        elif self._handle_doc_change(src_path):
            self._schedule_post_sync()

    def _clear_directory_index(self, rel_dir: str) -> bool:
        """Removes all files under a relative directory from index and resolver."""
        prefix = f"{rel_dir}/" if rel_dir not in ("", ".") else ""
        all_files = self.db.get_all_indexed_file_paths()
        deleted_any = False
        for f in all_files:
            if f.startswith(prefix) or f == rel_dir:
                self.db.clear_file(f)
                self.resolver.remove_file(f)
                deleted_any = True
        return deleted_any

    def _clear_single_file_index(self, rel_path: str) -> bool:
        """Removes a single file from resolver and database index."""
        was_indexed = (
            rel_path in self.resolver.known_files or self.db.get_file_hash(rel_path) is not None
        )
        if was_indexed:
            self.resolver.remove_file(rel_path)
            self.db.clear_file(rel_path)
            return True
        return False

    def _index_directory_tree(self, dest_path: str) -> None:
        """Recursively discovers and indexes files under a target directory."""
        dest_dir = Path(dest_path)
        if not dest_dir.exists():
            return
        for root, dirs, files in os.walk(dest_dir):
            dirs[:] = [d for d in dirs if not self._should_ignore(str(Path(root) / d))]
            for file in files:
                full_file = Path(root) / file
                if not self._should_ignore(str(full_file)):
                    rel_f = self._to_rel_path(str(full_file))
                    indexed = index_file(
                        self.db,
                        str(full_file),
                        project_root=self.root_path,
                        resolver=self.resolver,
                        force=True,
                    )
                    if indexed:
                        self.resolver.add_file(rel_f)

    def on_deleted(self, event):
        src_path = str(event.src_path)
        if self._should_ignore(src_path):
            return

        if self._handle_doc_change(src_path, is_deleted=True):
            self._schedule_post_sync()
            return

        if event.is_directory:
            rel_dir = self._to_rel_path(src_path)
            if self._clear_directory_index(rel_dir):
                print(f"Directory deleted: {rel_dir} (cleared indexed files)")
                self._schedule_post_sync()
            return

        rel_path = self._to_rel_path(src_path)
        if self._clear_single_file_index(rel_path):
            print(f"File deleted: {rel_path}")
            self._schedule_post_sync()

    def on_moved(self, event):
        src_path = str(event.src_path)
        dest_path = str(event.dest_path)

        src_ignored = self._should_ignore(src_path)
        dest_ignored = self._should_ignore(dest_path)
        if src_ignored and dest_ignored:
            return

        rel_src = self._to_rel_path(src_path)
        rel_dest = self._to_rel_path(dest_path)

        if event.is_directory:
            if not src_ignored:
                self._clear_directory_index(rel_src)
            if not dest_ignored:
                self._index_directory_tree(dest_path)
            print(f"Directory moved: {rel_src} -> {rel_dest}")
            self._schedule_post_sync()
            return

        print(f"File moved: {rel_src} -> {rel_dest}")
        changed = False
        if not src_ignored:
            changed = self._clear_single_file_index(rel_src)
        if not dest_ignored:
            indexed = index_file(
                self.db,
                dest_path,
                project_root=self.root_path,
                resolver=self.resolver,
                force=True,
            )
            if indexed:
                self.resolver.add_file(rel_dest)
                changed = True

        if changed:
            self._schedule_post_sync()


def start_observer(path: str, db: IndexerDB, callback: Callable[[], None] | None = None) -> Any:
    """Start the observer and return it (non-blocking)."""
    path_obj = Path(path).resolve()
    if not path_obj.exists():
        raise FileNotFoundError(f"Watcher target path does not exist: {path_obj}")

    event_handler = CodeChangeHandler(db, callback, root_path=str(path_obj))
    observer = Observer()
    observer.schedule(event_handler, str(path_obj), recursive=True)
    observer._event_handler = event_handler  # type: ignore[attr-defined]
    observer.start()
    print(f"VibeTrace Watcher started on {path_obj}")
    return observer


def stop_observer(observer: Any) -> None:
    """Stop the filesystem observer and cancel any pending debounce timers."""
    if observer is None:
        return
    handler = getattr(observer, "_event_handler", None)
    if handler is not None and hasattr(handler, "stop"):
        try:
            handler.stop()
        except Exception:
            pass
    try:
        observer.stop()
        observer.join(timeout=2.0)
    except Exception:
        pass


def start_watcher_blocking(path: str):
    """Blocking version for CLI usage."""
    from vibetrace.services.graph_service import get_context_for_path

    db, root = get_context_for_path(path, auto_sync=False)
    observer = start_observer(str(root), db)
    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        pass
    finally:
        stop_observer(observer)
        try:
            db.close()
        except Exception:
            pass


if __name__ == "__main__":
    import sys

    if len(sys.argv) < 2:
        print("Usage: python -m vibetrace.indexer.watcher <directory>")
    else:
        start_watcher_blocking(sys.argv[1])
