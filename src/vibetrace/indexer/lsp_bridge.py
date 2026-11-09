"""LSP (Language Server Protocol) Hybrid Bridge for resolving dynamic call edges."""

from __future__ import annotations

import concurrent.futures
import json
import logging
import os
import shutil
import subprocess
import threading
from concurrent.futures import TimeoutError as FuturesTimeoutError
from pathlib import Path
from typing import Any
from urllib.parse import unquote, urlparse

from vibetrace.indexer.db import IndexerDB
from vibetrace.indexer.db_queries import _clean_path
from vibetrace.indexer.db_retry import with_write_retry

logger = logging.getLogger(__name__)


class LSPClient:
    """Lightweight stdio JSON-RPC 2.0 client for LSP servers."""

    def __init__(self, command: list[str], root_path: Path) -> None:
        self.command = command
        self.root_path = root_path.resolve()
        self.process: subprocess.Popen[bytes] | None = None
        self._req_id = 0
        self._pending_requests: dict[int, concurrent.futures.Future[Any]] = {}
        self._reader_thread: threading.Thread | None = None
        self._lock = threading.Lock()
        self._write_lock = threading.Lock()
        self._running = False
        self._opened_uris: set[str] = set()

    def start(self, timeout: float = 5.0) -> bool:
        """Start the LSP server process and initialize the session."""
        executable = shutil.which(self.command[0])
        if not executable:
            return False
        cmd = [executable, *self.command[1:]]
        try:
            self.process = subprocess.Popen(
                cmd,
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
                cwd=str(self.root_path),
            )
            self._running = True
            self._reader_thread = threading.Thread(target=self._reader_loop, daemon=True)
            self._reader_thread.start()

            root_uri = self.root_path.as_uri()
            init_res = self.send_request(
                "initialize",
                {
                    "processId": os.getpid(),
                    "rootUri": root_uri,
                    "capabilities": {
                        "textDocument": {
                            "definition": {"dynamicRegistration": False},
                        }
                    },
                },
                timeout=timeout,
            )
            if init_res is not None:
                self.send_notification("initialized", {})
                return True
            self.stop()
        except (subprocess.SubprocessError, OSError):
            self.stop()
        return False

    def _reader_loop(self) -> None:
        """Continuously read LSP JSON-RPC frames from process stdout."""
        proc = self.process
        if not proc or not proc.stdout:
            return

        while self._running:
            try:
                content_length: int | None = None
                while self._running:
                    line = proc.stdout.readline().decode("latin1")
                    if not line:
                        self._running = False
                        break
                    if line == "\r\n":
                        break
                    if line.lower().startswith("content-length:"):
                        try:
                            content_length = int(line.split(":", 1)[1].strip())
                        except ValueError as ve:
                            logger.debug("Malformed LSP Content-Length header: %s (%s)", line, ve)
                            content_length = None

                if not self._running or content_length is None:
                    break

                body_bytes = proc.stdout.read(content_length)
                if len(body_bytes) < content_length:
                    break

                msg = json.loads(body_bytes.decode("utf-8"))
                req_id = msg.get("id")
                # Distinguish responses from server-initiated requests
                if req_id is not None and "method" not in msg:
                    with self._lock:
                        fut = self._pending_requests.pop(req_id, None)
                    if fut and not fut.done():
                        fut.set_result(msg.get("result"))
                elif req_id is not None and "method" in msg:
                    # Reply with null result to server-initiated requests
                    resp_dict = {"jsonrpc": "2.0", "id": req_id, "result": None}
                    resp = json.dumps(resp_dict).encode("utf-8")
                    hdr = f"Content-Length: {len(resp)}\r\n\r\n".encode("ascii")
                    with self._write_lock:
                        if self.process and self.process.stdin:
                            try:
                                self.process.stdin.write(hdr + resp)
                                self.process.stdin.flush()
                            except (BrokenPipeError, OSError):
                                pass
            except Exception as e:
                logger.debug("LSP reader loop error: %s", e)
                break

        with self._lock:
            for fut in self._pending_requests.values():
                if not fut.done():
                    fut.cancel()
            self._pending_requests.clear()

    def send_notification(self, method: str, params: dict[str, Any]) -> None:
        """Send a one-way notification without expecting a response."""
        if not self.process or not self.process.stdin:
            return
        payload = json.dumps({"jsonrpc": "2.0", "method": method, "params": params}).encode("utf-8")
        header = f"Content-Length: {len(payload)}\r\n\r\n".encode("ascii")
        with self._write_lock:
            try:
                self.process.stdin.write(header + payload)
                self.process.stdin.flush()
            except (BrokenPipeError, OSError):
                pass

    def send_request(self, method: str, params: dict[str, Any], timeout: float = 3.0) -> Any:
        """Send a JSON-RPC request and wait with an enforceable timeout for the response."""
        if not self.process or not self.process.stdin:
            return None

        with self._lock:
            self._req_id += 1
            req_id = self._req_id
            future: concurrent.futures.Future[Any] = concurrent.futures.Future()
            self._pending_requests[req_id] = future

        payload = json.dumps(
            {"jsonrpc": "2.0", "id": req_id, "method": method, "params": params}
        ).encode("utf-8")
        header = f"Content-Length: {len(payload)}\r\n\r\n".encode("ascii")

        try:
            with self._write_lock:
                self.process.stdin.write(header + payload)
                self.process.stdin.flush()
            return future.result(timeout=timeout)
        except (FuturesTimeoutError, TimeoutError):
            return None
        except Exception:
            return None
        finally:
            with self._lock:
                self._pending_requests.pop(req_id, None)

    def ensure_document_opened(self, file_path: Path) -> None:
        """Send textDocument/didOpen if not previously opened."""
        uri = file_path.resolve().as_uri()
        if uri in self._opened_uris:
            return
        try:
            text = file_path.read_text(encoding="utf-8", errors="replace")
            ext = file_path.suffix.lower()
            lang_id = {
                ".py": "python",
                ".ts": "typescript",
                ".js": "javascript",
                ".rs": "rust",
                ".go": "go",
            }.get(ext, "plaintext")
            self.send_notification(
                "textDocument/didOpen",
                {
                    "textDocument": {
                        "uri": uri,
                        "languageId": lang_id,
                        "version": 1,
                        "text": text,
                    }
                },
            )
            self._opened_uris.add(uri)
        except Exception as e:
            logger.debug("Failed to didOpen %s: %s", file_path, e)

    def get_definition(
        self, file_path: Path, line: int, character: int = 0
    ) -> tuple[str, int] | None:
        """Query textDocument/definition and return (relative_file_path, 1-indexed line)."""
        self.ensure_document_opened(file_path)
        file_uri = file_path.resolve().as_uri()
        res = self.send_request(
            "textDocument/definition",
            {
                "textDocument": {"uri": file_uri},
                "position": {"line": max(0, line - 1), "character": character},
            },
        )
        if not res:
            return None

        # LSP result can be Location, LocationLink, or list of Locations
        item = res[0] if isinstance(res, list) and res else res
        if not isinstance(item, dict):
            return None

        target_uri = item.get("uri") or item.get("targetUri")
        target_range = item.get("range") or item.get("targetRange")
        if not target_uri or not target_range:
            return None

        raw_uri = unquote(target_uri)
        u = urlparse(raw_uri)
        if os.name == "nt" and u.netloc and len(u.netloc) == 2 and u.netloc[1] == ":":
            parsed_path = f"{u.netloc}{u.path}"
        else:
            parsed_path = u.path

        if os.name == "nt" and parsed_path.startswith("/"):
            parsed_path = parsed_path[1:]
        target_file = Path(parsed_path)

        try:
            rel_file = str(target_file.relative_to(self.root_path)).replace("\\", "/")
        except ValueError:
            rel_file = str(target_file).replace("\\", "/")

        start_line = target_range.get("start", {}).get("line", 0) + 1
        return rel_file, start_line

    def stop(self) -> None:
        """Terminate the LSP server process and reader thread conforming to LSP spec."""
        if self.process:
            try:
                try:
                    self.send_request("shutdown", {}, timeout=1.0)
                except Exception:
                    pass
                self.send_notification("exit", {})
            except Exception:
                pass

        self._running = False
        with self._lock:
            for fut in self._pending_requests.values():
                if not fut.done():
                    fut.cancel()
            self._pending_requests.clear()

        if self.process:
            try:
                if self.process.stdin:
                    self.process.stdin.close()
                self.process.terminate()
                self.process.wait(timeout=1.0)
            except Exception:
                try:
                    self.process.kill()
                except Exception:
                    pass
            self.process = None
        if self._reader_thread and self._reader_thread.is_alive():
            self._reader_thread.join(timeout=0.5)
            self._reader_thread = None


def resolve_unresolved_edges(
    db: IndexerDB,
    project_root: Path,
    lsp_client: LSPClient,
    verbose: bool = True,
    modified_files: set[str] | list[str] | None = None,
) -> int:
    """Query LSP for unresolved call targets and rewire them to indexed definitions.

    If modified_files is provided, restricts resolution strictly to unresolved
    edges originating from those files for ultra-fast incremental reindexing.
    """
    clean_modified: list[str] = []
    if modified_files is not None:
        clean_modified = [
            f.replace("\\", "/").strip("/")
            for f in modified_files
            if f and f.replace("\\", "/").strip("/")
        ]
        if not clean_modified:
            return 0

    base_query = """
        SELECT e.id, e.from_node_id, e.to_node_id, e.call_line, n.file_path as caller_file
        FROM edges e
        JOIN nodes n ON e.from_node_id = n.id
        LEFT JOIN nodes target ON e.to_node_id = target.id
        WHERE target.id IS NULL
          AND e.call_line IS NOT NULL
          AND e.relation_type IN ('calls', 'references')
    """
    if clean_modified:
        placeholders = ", ".join("?" for _ in clean_modified)
        sql_unresolved = f"{base_query}\n          AND n.file_path IN ({placeholders})"
        params: tuple[Any, ...] = tuple(clean_modified)
    else:
        sql_unresolved = base_query
        params = ()

    with db._get_conn() as conn:
        cursor = conn.execute(sql_unresolved, params)
        rows = [dict(r) for r in cursor.fetchall()]

    if not rows:
        return 0

    # Resolve definitions via LSP OUTSIDE of any database transaction
    rewire_candidates: list[tuple[int, str, int]] = []
    file_lines_cache: dict[Path, list[str]] = {}

    for row in rows:
        caller_file = project_root / row["caller_file"]
        call_line = row["call_line"]
        if not caller_file.exists():
            continue

        if caller_file not in file_lines_cache:
            try:
                file_lines_cache[caller_file] = caller_file.read_text(
                    encoding="utf-8", errors="replace"
                ).splitlines()
            except Exception as e:
                logger.debug("Failed to read caller file %s: %s", caller_file, e)
                file_lines_cache[caller_file] = []

        lines = file_lines_cache[caller_file]
        char_offset = 0
        if 1 <= call_line <= len(lines):
            line_str = lines[call_line - 1]
            char_offset = len(line_str) - len(line_str.lstrip())
            raw_target = str(row.get("to_node_id") or "")
            short_sym = raw_target.split("::")[-1].split(".")[-1].strip()
            if short_sym:
                idx = line_str.find(short_sym)
                if idx >= 0:
                    char_offset = idx

        def_target = lsp_client.get_definition(caller_file, call_line, character=char_offset)
        if not def_target:
            continue

        target_file, target_line = def_target
        clean_target = _clean_path(target_file)
        rewire_candidates.append((row["id"], clean_target, target_line))

    if not rewire_candidates:
        return 0

    # Match and batch-update rewired targets under a fast retryable write transaction
    find_node_sql = """
        SELECT id FROM nodes
        WHERE LOWER(file_path) = LOWER(?) AND start_line <= ? AND end_line >= ?
        ORDER BY (end_line - start_line) ASC
        LIMIT 1
    """

    @with_write_retry()
    def _apply_rewires() -> int:
        count = 0
        with db._get_conn() as conn:
            for edge_id, target_file, target_line in rewire_candidates:
                matched = conn.execute(
                    find_node_sql, (target_file, target_line, target_line)
                ).fetchone()
                if matched:
                    new_target_id = matched["id"]
                    conn.execute(
                        "UPDATE edges SET to_node_id = ? WHERE id = ?",
                        (new_target_id, edge_id),
                    )
                    count += 1
            if count > 0:
                conn.commit()
        return count

    resolved_count = _apply_rewires()

    if verbose and resolved_count > 0:
        print(f"LSP Hybrid Bridge: Rewired {resolved_count} polymorphic/re-exported call edges.")
    return resolved_count


def get_default_lsp_command(project_root: Path) -> list[str] | None:
    """Detect available LSP language servers installed on the host machine."""
    candidates = [
        ("basedpyright-langserver", ["basedpyright-langserver", "--stdio"]),
        ("pyright-langserver", ["pyright-langserver", "--stdio"]),
        ("pylsp", ["pylsp"]),
        ("typescript-language-server", ["typescript-language-server", "--stdio"]),
        ("rust-analyzer", ["rust-analyzer"]),
        ("gopls", ["gopls"]),
    ]
    for binary, cmd in candidates:
        if shutil.which(binary):
            return cmd
    return None
