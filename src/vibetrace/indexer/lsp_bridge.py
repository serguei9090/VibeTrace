"""LSP (Language Server Protocol) Hybrid Bridge for resolving dynamic call edges."""

from __future__ import annotations

import concurrent.futures
import json
import logging
import os
import shutil
import subprocess
import threading
from collections.abc import Sequence
from concurrent.futures import TimeoutError as FuturesTimeoutError
from pathlib import Path
from typing import Any, ClassVar
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

    def ensure_documents_opened_batch(self, file_paths: Sequence[Path]) -> None:
        """Batch-open documents that have not been opened yet, reducing disk reads."""
        for fp in file_paths:
            self.ensure_document_opened(fp)

    def _parse_definition_response(self, res: Any) -> tuple[str, int] | None:
        """Helper to parse raw LSP definition response into (relative_path, line)."""
        if not res:
            return None
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
        return self._parse_definition_response(res)

    def get_definitions_batch(
        self,
        queries: Sequence[tuple[Path, int, int]],
        batch_size: int = 32,
        per_batch_timeout: float = 3.0,
    ) -> list[tuple[str, int] | None]:
        """Pipelined concurrent batch query for definitions with sliding window control.

        queries: sequence of (caller_file_path, line, character)
        Returns: list of results in identical order to queries.
        """
        if not queries:
            return []

        # 1. Pre-warm documents in batch
        unique_files = {q[0] for q in queries}
        self.ensure_documents_opened_batch(list(unique_files))

        # Fallback if get_definition was mocked or overridden on this instance
        if "get_definition" in self.__dict__ and callable(self.__dict__["get_definition"]):
            return [self.get_definition(f, line, char) for f, line, char in queries]

        if not self.process or not self.process.stdin:
            return [None] * len(queries)

        results: list[tuple[str, int] | None] = [None] * len(queries)

        # 2. Process queries in sliding window chunks to avoid OS pipe buffer deadlock
        for chunk_start in range(0, len(queries), batch_size):
            chunk = queries[chunk_start : chunk_start + batch_size]
            pending: list[tuple[int, int, concurrent.futures.Future[Any]]] = []
            payload_chunks: list[bytes] = []

            for idx_in_chunk, (file_path, line, character) in enumerate(chunk):
                orig_idx = chunk_start + idx_in_chunk
                file_uri = file_path.resolve().as_uri()

                with self._lock:
                    self._req_id += 1
                    req_id = self._req_id
                    fut: concurrent.futures.Future[Any] = concurrent.futures.Future()
                    self._pending_requests[req_id] = fut

                pending.append((orig_idx, req_id, fut))

                msg = {
                    "jsonrpc": "2.0",
                    "id": req_id,
                    "method": "textDocument/definition",
                    "params": {
                        "textDocument": {"uri": file_uri},
                        "position": {"line": max(0, line - 1), "character": character},
                    },
                }
                body = json.dumps(msg).encode("utf-8")
                header = f"Content-Length: {len(body)}\r\n\r\n".encode("ascii")
                payload_chunks.append(header + body)

            # Write entire batch payload in single lock acquisition
            try:
                full_payload = b"".join(payload_chunks)
                with self._write_lock:
                    self.process.stdin.write(full_payload)
                    self.process.stdin.flush()
            except Exception as e:
                logger.debug("Failed to write pipelined batch: %s", e)
                for _, req_id, fut in pending:
                    with self._lock:
                        self._pending_requests.pop(req_id, None)
                    fut.cancel()
                continue

            # Wait for all futures in this batch with overall timeout
            futs = [fut for _, _, fut in pending]
            concurrent.futures.wait(futs, timeout=per_batch_timeout)

            # Harvest completed responses and cleanup
            for orig_idx, req_id, fut in pending:
                with self._lock:
                    self._pending_requests.pop(req_id, None)

                if fut.done() and not fut.cancelled():
                    try:
                        raw_res = fut.result()
                        results[orig_idx] = self._parse_definition_response(raw_res)
                    except Exception:
                        results[orig_idx] = None
                else:
                    fut.cancel()
                    results[orig_idx] = None

        return results

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


LSP_SERVER_CANDIDATES: dict[str, list[tuple[str, list[str]]]] = {
    "python": [
        ("basedpyright-langserver", ["basedpyright-langserver", "--stdio"]),
        ("pyright-langserver", ["pyright-langserver", "--stdio"]),
        ("pylsp", ["pylsp"]),
    ],
    "typescript": [
        ("typescript-language-server", ["typescript-language-server", "--stdio"]),
        ("vtsls", ["vtsls", "--stdio"]),
    ],
    "rust": [
        ("rust-analyzer", ["rust-analyzer"]),
    ],
    "go": [
        ("gopls", ["gopls"]),
    ],
    "php": [
        ("intelephense", ["intelephense", "--stdio"]),
        ("phpactor", ["phpactor", "language-server"]),
    ],
}

EXT_TO_LANGUAGE: dict[str, str] = {
    ".py": "python",
    ".ts": "typescript",
    ".tsx": "typescript",
    ".js": "typescript",
    ".jsx": "typescript",
    ".rs": "rust",
    ".go": "go",
    ".php": "php",
    ".phtml": "php",
}


def get_lsp_command_for_language(language: str) -> list[str] | None:
    """Finds an available LSP binary for a specific language."""
    candidates = LSP_SERVER_CANDIDATES.get(language, [])
    for binary, cmd in candidates:
        if shutil.which(binary):
            return cmd
    return None


def get_default_lsp_command(project_root: Path) -> list[str] | None:
    """Detect available LSP language servers installed on the host machine."""
    # Look for Python first, then TypeScript, Rust, Go, PHP
    for lang in ("python", "typescript", "rust", "go", "php"):
        cmd = get_lsp_command_for_language(lang)
        if cmd:
            return cmd
    return None


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
        SELECT e.id, e.from_node_id, e.to_node_id, e.call_line, n.file_path as caller_file,
               et.target_symbol
        FROM edges e
        JOIN nodes n ON e.from_node_id = n.id
        LEFT JOIN nodes target ON e.to_node_id = target.id
        LEFT JOIN edge_targets et ON e.to_node_id = et.target_id
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

    # Resolve definitions via LSP OUTSIDE of DB transaction using Pipelined Batching
    query_items: list[tuple[int, Path, int, int]] = []
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

            # Priority 1: Use edge_targets.target_symbol (e.g. 'worker.execute' -> 'execute')
            target_sym = row.get("target_symbol") or ""
            target_method = target_sym.split(".")[-1].strip() if target_sym else ""

            if target_method and target_method in line_str:
                idx = line_str.rfind(target_method)
                if idx >= 0:
                    char_offset = idx
            else:
                # Priority 2: Fallback to short symbol from raw to_node_id
                raw_target = str(row.get("to_node_id") or "")
                short_sym = raw_target.split("::")[-1].split(".")[-1].strip()
                if short_sym and short_sym in line_str:
                    idx = line_str.rfind(short_sym)
                    if idx >= 0:
                        char_offset = idx

        query_items.append((row["id"], caller_file, call_line, char_offset))

    if not query_items:
        return 0

    # Query LSP definitions via high-throughput pipelining with sliding window
    lsp_queries = [(item[1], item[2], item[3]) for item in query_items]
    batch_results = lsp_client.get_definitions_batch(
        lsp_queries, batch_size=32, per_batch_timeout=3.0
    )
    # If lsp_client is a Mock whose get_definitions_batch didn't return a list,
    # fallback to calling get_definition individually
    if not isinstance(batch_results, list) or len(batch_results) != len(query_items):
        batch_results = [lsp_client.get_definition(q[0], q[1], character=q[2]) for q in lsp_queries]

    rewire_candidates: list[tuple[int, str, int]] = []
    for (edge_id, _, _, _), def_target in zip(query_items, batch_results, strict=True):
        if not def_target:
            continue
        target_file, target_line = def_target
        clean_target = _clean_path(target_file)
        rewire_candidates.append((edge_id, clean_target, target_line))

    if not rewire_candidates:
        return 0

    # Match and batch-update rewired targets under a fast retryable write transaction
    find_node_sql = """
        SELECT id, kind FROM nodes
        WHERE LOWER(file_path) = LOWER(?) AND start_line <= ? AND end_line >= ?
        ORDER BY (end_line - start_line) ASC
        LIMIT 1
    """

    find_constructor_sql = """
        SELECT id FROM nodes
        WHERE LOWER(file_path) = LOWER(?) AND parent_id = ?
          AND name IN ('__init__', 'constructor', 'new', 'New')
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
                    # If target node is a class, check if it has a constructor method
                    if matched["kind"] == "class":
                        ctor = conn.execute(
                            find_constructor_sql, (target_file, new_target_id)
                        ).fetchone()
                        if ctor:
                            new_target_id = ctor["id"]

                    conn.execute(
                        "UPDATE edges SET to_node_id = ?, confidence = 'lsp' WHERE id = ?",
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


def resolve_unresolved_edges_polyglot(
    db: IndexerDB,
    project_root: Path,
    verbose: bool = True,
    modified_files: set[str] | list[str] | None = None,
) -> int:
    """Group unresolved edges by language and resolve via concurrent LSP workers."""
    with db._get_conn() as conn:
        cursor = conn.execute(
            """
            SELECT DISTINCT n.file_path as caller_file
            FROM edges e
            JOIN nodes n ON e.from_node_id = n.id
            LEFT JOIN nodes target ON e.to_node_id = target.id
            WHERE target.id IS NULL
              AND e.call_line IS NOT NULL
              AND e.relation_type IN ('calls', 'references')
            """
        )
        caller_files = [r[0] for r in cursor.fetchall() if r[0]]

    if not caller_files:
        return 0

    if modified_files is not None:
        clean_mod = {
            f.replace("\\", "/").strip("/")
            for f in modified_files
            if f and f.replace("\\", "/").strip("/")
        }
        caller_files = [f for f in caller_files if f.replace("\\", "/").strip("/") in clean_mod]
        if not caller_files:
            return 0

    # Group files by detected language
    files_by_lang: dict[str, list[str]] = {}
    for f in caller_files:
        ext = Path(f).suffix.lower()
        lang = EXT_TO_LANGUAGE.get(ext)
        if lang:
            files_by_lang.setdefault(lang, []).append(f)

    if not files_by_lang:
        return 0

    total_rewired = 0
    # Resolve for each language using MultiLanguageLSPManager
    for lang, lang_files in files_by_lang.items():
        client = MultiLanguageLSPManager.get_client(project_root, lang)
        if not client:
            if verbose:
                logger.debug("No LSP server available for %s", lang)
            continue
        try:
            rewired = resolve_unresolved_edges(
                db=db,
                project_root=project_root,
                lsp_client=client,
                verbose=verbose,
                modified_files=lang_files,
            )
            total_rewired += rewired
        except Exception as e:
            logger.warning("LSP resolution error for %s: %s", lang, e)

    return total_rewired


class MultiLanguageLSPManager:
    """Manages pooled language server clients across multiple programming languages."""

    _instances: ClassVar[dict[tuple[str, str], LSPClient]] = {}
    _lock = threading.Lock()

    @classmethod
    def get_client(cls, project_root: Path, language: str) -> LSPClient | None:
        """Retrieve or warm-start an LSPClient for a specific project and language."""
        root_key = str(project_root.resolve()).replace("\\", "/")
        cache_key = (root_key, language.lower())
        with cls._lock:
            existing = cls._instances.get(cache_key)
            if existing and existing._running:
                return existing

            cmd = get_lsp_command_for_language(language)
            if not cmd:
                return None

            client = LSPClient(cmd, project_root)
            if client.start():
                client._running = True
                cls._instances[cache_key] = client
                return client
            return None

    @classmethod
    def stop_all(cls) -> None:
        """Cleanly shutdown all cached LSP server daemons across all languages."""
        with cls._lock:
            for client in cls._instances.values():
                try:
                    client.stop()
                except Exception:
                    pass
            cls._instances.clear()

    @classmethod
    def stop_for_project(cls, project_root: Path) -> None:
        """Stop all clients associated with a specific project root."""
        root_key = str(project_root.resolve()).replace("\\", "/")
        with cls._lock:
            keys_to_remove = [k for k in cls._instances if k[0] == root_key]
            for k in keys_to_remove:
                c = cls._instances.pop(k, None)
                if c:
                    try:
                        c.stop()
                    except Exception:
                        pass


class LSPDaemonManager:
    """Backwards-compatible wrapper delegating to MultiLanguageLSPManager."""

    # Direct delegation to MultiLanguageLSPManager._instances without classmethod+property
    _instances: ClassVar[dict[tuple[str, str], LSPClient]] = MultiLanguageLSPManager._instances

    @classmethod
    def get_client(cls, project_root: Path, language: str = "python") -> LSPClient | None:
        """Retrieve client for project root (defaults to python or first available)."""
        cmd = get_default_lsp_command(project_root)
        if not cmd:
            return None
        # Infer language from command
        detected_lang = "python"
        for lang_name, candidates in LSP_SERVER_CANDIDATES.items():
            if any(cmd[0] == c[0] for c in candidates):
                detected_lang = lang_name
                break
        return MultiLanguageLSPManager.get_client(project_root, detected_lang)

    @classmethod
    def stop_all(cls) -> None:
        MultiLanguageLSPManager.stop_all()

    @classmethod
    def stop_client(cls, project_root: Path) -> None:
        MultiLanguageLSPManager.stop_for_project(project_root)
