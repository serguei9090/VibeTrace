"""LSP (Language Server Protocol) Hybrid Bridge for resolving dynamic call edges."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
from concurrent.futures import ThreadPoolExecutor
from concurrent.futures import TimeoutError as FuturesTimeoutError
from pathlib import Path
from typing import Any
from urllib.parse import unquote, urlparse

from vibegraph.indexer.db import IndexerDB


class LSPClient:
    """Lightweight stdio JSON-RPC 2.0 client for LSP servers."""

    def __init__(self, command: list[str], root_path: Path) -> None:
        self.command = command
        self.root_path = root_path.resolve()
        self.process: subprocess.Popen[bytes] | None = None
        self._req_id = 0
        self._executor: ThreadPoolExecutor | None = None

    def start(self, timeout: float = 5.0) -> bool:
        """Start the LSP server process and initialize the session."""
        if not shutil.which(self.command[0]):
            return False
        try:
            self._executor = ThreadPoolExecutor(max_workers=1)
            self.process = subprocess.Popen(
                self.command,
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
                cwd=str(self.root_path),
            )
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
        except (subprocess.SubprocessError, OSError):
            self.stop()
        return False

    def send_notification(self, method: str, params: dict[str, Any]) -> None:
        """Send a one-way notification without expecting a response."""
        if not self.process or not self.process.stdin:
            return
        payload = json.dumps({"jsonrpc": "2.0", "method": method, "params": params}).encode("utf-8")
        header = f"Content-Length: {len(payload)}\r\n\r\n".encode("ascii")
        try:
            self.process.stdin.write(header + payload)
            self.process.stdin.flush()
        except (BrokenPipeError, OSError):
            pass

    def _read_response(self, req_id: int) -> Any:
        """Read stdout until the matching response id is found."""
        if not self.process or not self.process.stdout:
            return None
        while True:
            content_length: int | None = None
            while True:
                line = self.process.stdout.readline().decode("latin1")
                if not line or line == "\r\n":
                    break
                if line.lower().startswith("content-length:"):
                    content_length = int(line.split(":", 1)[1].strip())

            if content_length is None:
                return None

            body_bytes = self.process.stdout.read(content_length)
            msg = json.loads(body_bytes.decode("utf-8"))
            if msg.get("id") == req_id:
                return msg.get("result")

    def send_request(self, method: str, params: dict[str, Any], timeout: float = 3.0) -> Any:
        """Send a JSON-RPC request and wait with an enforceable timeout for the response."""
        if not self.process or not self.process.stdin or not self.process.stdout:
            return None
        if not self._executor:
            self._executor = ThreadPoolExecutor(max_workers=1)

        self._req_id += 1
        req_id = self._req_id
        payload = json.dumps(
            {"jsonrpc": "2.0", "id": req_id, "method": method, "params": params}
        ).encode("utf-8")
        header = f"Content-Length: {len(payload)}\r\n\r\n".encode("ascii")

        try:
            self.process.stdin.write(header + payload)
            self.process.stdin.flush()

            future = self._executor.submit(self._read_response, req_id)
            return future.result(timeout=timeout)
        except (FuturesTimeoutError, TimeoutError):
            return None
        except Exception:
            return None

    def get_definition(
        self, file_path: Path, line: int, character: int = 0
    ) -> tuple[str, int] | None:
        """Query textDocument/definition and return (relative_file_path, 1-indexed line)."""
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

        parsed_path = unquote(urlparse(target_uri).path)
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
        """Terminate the LSP server process and worker threads."""
        if self._executor:
            try:
                self._executor.shutdown(wait=False, cancel_futures=True)
            except Exception:
                pass
            self._executor = None
        if self.process:
            try:
                self.send_notification("exit", {})
                self.process.terminate()
                self.process.wait(timeout=1.0)
            except Exception:
                try:
                    self.process.kill()
                except Exception:
                    pass
            self.process = None


def resolve_unresolved_edges(
    db: IndexerDB,
    project_root: Path,
    lsp_client: LSPClient,
    verbose: bool = True,
) -> int:
    """Query LSP for unresolved call targets and rewire them to indexed definitions."""
    sql_unresolved = """
        SELECT e.id, e.from_node_id, e.to_node_id, e.call_line, n.file_path as caller_file
        FROM edges e
        JOIN nodes n ON e.from_node_id = n.id
        LEFT JOIN nodes target ON e.to_node_id = target.id
        WHERE target.id IS NULL
          AND e.call_line IS NOT NULL
          AND e.relation_type IN ('calls', 'references')
    """
    with db._get_conn() as conn:
        cursor = conn.execute(sql_unresolved)
        rows = cursor.fetchall()

    if not rows:
        return 0

    resolved_count = 0
    with db._get_conn() as conn:
        for row in rows:
            caller_file = project_root / row["caller_file"]
            call_line = row["call_line"]
            if not caller_file.exists():
                continue

            def_target = lsp_client.get_definition(caller_file, call_line)
            if not def_target:
                continue

            target_file, target_line = def_target
            # Find closest node in target file covering the line
            find_node_sql = """
                SELECT id FROM nodes
                WHERE file_path = ? AND start_line <= ? AND end_line >= ?
                ORDER BY (end_line - start_line) ASC
                LIMIT 1
            """
            matched = conn.execute(
                find_node_sql, (target_file, target_line, target_line)
            ).fetchone()
            if matched:
                new_target_id = matched["id"]
                conn.execute(
                    "UPDATE edges SET to_node_id = ? WHERE id = ?",
                    (new_target_id, row["id"]),
                )
                resolved_count += 1

        if resolved_count > 0:
            conn.commit()

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
