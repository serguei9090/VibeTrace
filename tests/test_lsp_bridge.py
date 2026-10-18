"""Unit tests for LSP Hybrid Bridge and dynamic type resolution."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

from vibegraph.indexer.db import Edge, IndexerDB, Node
from vibegraph.indexer.lsp_bridge import (
    LSPClient,
    get_default_lsp_command,
    resolve_unresolved_edges,
)


def test_get_default_lsp_command() -> None:
    """Verify get_default_lsp_command detects binary or returns None without crashing."""
    cmd = get_default_lsp_command(Path.cwd())
    if cmd is not None:
        assert isinstance(cmd, list)
        assert len(cmd) >= 1


def test_lsp_client_lifecycle_missing_binary(tmp_path: Path) -> None:
    """Verify LSPClient fails gracefully when command is not found."""
    client = LSPClient(["non_existent_lsp_server_binary"], tmp_path)
    assert client.start() is False
    client.stop()


def test_resolve_unresolved_edges_with_mock_lsp(tmp_path: Path) -> None:
    """Verify unresolved call edges are rewired when LSP returns target definition."""
    db_path = str(tmp_path / "test_lsp.db")
    db = IndexerDB(db_path)

    # Node A in caller.py
    node_a = Node(
        id="caller_func_id",
        name="caller_func",
        qualified_name="caller_func",
        kind="function",
        file_path="caller.py",
        start_line=1,
        end_line=10,
    )
    # Node B in service.py (the actual definition)
    node_b = Node(
        id="target_service_id",
        name="actual_worker",
        qualified_name="actual_worker",
        kind="function",
        file_path="service.py",
        start_line=20,
        end_line=30,
    )
    db.upsert_node(node_a)
    db.upsert_node(node_b)

    # Edge from Node A to an unresolved phantom ID (e.g. from dynamic re-export)
    edge = Edge(
        from_node_id=node_a.id,
        to_node_id="phantom_unresolved_id",
        relation_type="calls",
        call_line=5,
    )
    db.upsert_edge(edge)

    # Verify edge currently points to phantom
    with db._get_conn() as conn:
        row = conn.execute(
            "SELECT to_node_id FROM edges WHERE from_node_id = ?", (node_a.id,)
        ).fetchone()
        assert row["to_node_id"] == "phantom_unresolved_id"

    # Create dummy caller file on disk
    caller_file = tmp_path / "caller.py"
    caller_file.write_text("def caller_func():\n    actual_worker()\n")

    # Mock LSP client returning (service.py, line 25)
    mock_client = MagicMock(spec=LSPClient)
    mock_client.get_definition.return_value = ("service.py", 25)

    rewired = resolve_unresolved_edges(db, tmp_path, mock_client, verbose=False)
    assert rewired == 1

    # Verify edge has been rewired to node_b.id!
    with db._get_conn() as conn:
        row = conn.execute(
            "SELECT to_node_id FROM edges WHERE from_node_id = ?", (node_a.id,)
        ).fetchone()
        assert row["to_node_id"] == node_b.id


def test_lsp_timeout_protection(tmp_path: Path) -> None:
    """Verify send_request returns None when response takes longer than timeout."""
    import time

    client = LSPClient(["mock_lsp"], tmp_path)
    client.process = MagicMock()
    client.process.stdin = MagicMock()
    client.process.stdout = MagicMock()

    def slow_read(req_id: int):
        time.sleep(0.3)
        return {"result": 42}

    client._read_response = slow_read  # type: ignore

    res = client.send_request("slow_method", {}, timeout=0.05)
    assert res is None
    client.stop()
