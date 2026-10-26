from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

from vibetrace import cli
from vibetrace.indexer.db import Edge, IndexerDB, Node
from vibetrace.indexer.watcher import start_watcher_blocking
from vibetrace.mcp.schemas import (
    DeleteSavedTraceInput,
    DependenciesInput,
    ImpactAnalysisInput,
    ListSavedTracesInput,
    ResponseFormat,
    SaveTraceInput,
    TracePathInput,
)
from vibetrace.mcp.server import (
    vibetrace_delete_saved_trace,
    vibetrace_get_dependencies,
    vibetrace_impact_analysis,
    vibetrace_list_saved_traces,
    vibetrace_save_trace,
    vibetrace_trace_path,
)
from vibetrace.services.saved_traces_service import (
    SavedTraceCreate,
    create_saved_trace,
    delete_saved_trace_by_name,
    get_saved_trace_by_name,
)


def test_saved_trace_delete_by_name(tmp_path: Path):
    """Verify service-level deletion by name."""
    db_path = tmp_path / "test_del.db"
    db = IndexerDB(str(db_path))

    create_saved_trace(
        db,
        SavedTraceCreate(
            name="CheckoutFlow",
            from_symbol="cart",
            to_symbol="charge",
            description="Payment path",
        ),
    )
    assert get_saved_trace_by_name(db, "CheckoutFlow") is not None

    deleted = delete_saved_trace_by_name(db, "CheckoutFlow")
    assert deleted is True
    assert get_saved_trace_by_name(db, "CheckoutFlow") is None
    assert delete_saved_trace_by_name(db, "NonExistent") is False
    db.close()


def test_saved_trace_delete_by_name_api():
    """Verify REST endpoint DELETE /api/saved-traces/by-name/{name}."""
    from vibetrace.server_api import app

    client = TestClient(app)
    # 1. Create trace
    client.post(
        "/api/saved-traces",
        json={
            "name": "ApiDeleteTest",
            "from_symbol": "src_sym",
            "to_symbol": "dst_sym",
            "max_depth": 3,
        },
    )

    # 2. Delete by name
    del_res = client.delete("/api/saved-traces/by-name/ApiDeleteTest")
    assert del_res.status_code == 200
    assert del_res.json() == {"status": "deleted", "name": "ApiDeleteTest"}

    # 3. 404 on repeat
    del_res_again = client.delete("/api/saved-traces/by-name/ApiDeleteTest")
    assert del_res_again.status_code == 404


def test_cli_trace_delete_saved(tmp_path: Path, capsys):
    """Verify CLI vibetrace trace --delete-saved <name>."""
    db_path = tmp_path / ".vibetrace" / "vibetrace.db"
    db_path.parent.mkdir(parents=True)
    db = IndexerDB(str(db_path))
    create_saved_trace(
        db,
        SavedTraceCreate(name="CliFlow", from_symbol="start", to_symbol="end", max_depth=4),
    )
    db.close()

    # Delete via CLI with --json
    code = cli.main(
        [
            "trace",
            "--delete-saved",
            "CliFlow",
            "--json",
            "--path",
            str(tmp_path),
            "--no-sync",
        ]
    )
    assert code == 0
    captured = capsys.readouterr()
    res = json.loads(captured.out)
    assert res == {"status": "deleted", "name": "CliFlow"}

    # Second delete returns code 1
    code_fail = cli.main(
        [
            "trace",
            "--delete-saved",
            "CliFlow",
            "--json",
            "--path",
            str(tmp_path),
            "--no-sync",
        ]
    )
    assert code_fail == 1


@pytest.mark.asyncio
async def test_mcp_saved_traces_suite(tmp_path: Path):
    """Verify MCP save, list, trace by saved_name, and delete saved trace tools."""
    db_path = tmp_path / "test_mcp_traces.db"
    db = IndexerDB(str(db_path))

    # Add symbols and edges to allow a valid trace
    n1 = Node(id="app.py:main", name="main", kind="function", file_path="app.py", start_line=1)
    n2 = Node(id="app.py:worker", name="worker", kind="function", file_path="app.py", start_line=10)
    db.upsert_node(n1)
    db.upsert_node(n2)
    db.upsert_edge(Edge(from_node_id=n1.id, to_node_id=n2.id, relation_type="calls", call_line=5))

    with patch("vibetrace.mcp.server._get_context_for_path", return_value=(db, tmp_path)):
        # 1. Save trace
        save_out = await vibetrace_save_trace(
            SaveTraceInput(
                name="MainToWorker",
                from_symbol="main",
                to_symbol="worker",
                description="Worker callpath",
                max_depth=3,
            )
        )
        assert "MainToWorker" in save_out

        # 2. List saved traces (JSON)
        list_json = await vibetrace_list_saved_traces(
            ListSavedTracesInput(response_format=ResponseFormat.JSON)
        )
        traces = json.loads(list_json)
        assert len(traces) == 1
        assert traces[0]["name"] == "MainToWorker"

        # 3. List saved traces (Markdown)
        list_md = await vibetrace_list_saved_traces(
            ListSavedTracesInput(response_format=ResponseFormat.MARKDOWN)
        )
        assert "## Saved Architectural Traces" in list_md
        assert "**MainToWorker**" in list_md

        # 4. Execute trace using saved_name
        trace_json = await vibetrace_trace_path(
            TracePathInput(saved_name="MainToWorker", response_format=ResponseFormat.JSON)
        )
        trace_res = json.loads(trace_json)
        assert trace_res["found"] is True
        assert trace_res["from_symbol"] == "main"
        assert trace_res["to_symbol"] == "worker"

        # 5. Delete saved trace
        del_out = await vibetrace_delete_saved_trace(DeleteSavedTraceInput(name="MainToWorker"))
        assert "Successfully deleted" in del_out

        # 6. Verify deletion
        list_after = await vibetrace_list_saved_traces(
            ListSavedTracesInput(response_format=ResponseFormat.JSON)
        )
        assert len(json.loads(list_after)) == 0

    db.close()


@pytest.mark.asyncio
async def test_mcp_impact_and_dependencies_json(tmp_path: Path):
    """Verify MCP impact analysis and dependencies honor response_format=JSON."""
    db_path = tmp_path / "test_mcp_dep.db"
    db = IndexerDB(str(db_path))

    n1 = Node(id="srv.py:run", name="run", kind="function", file_path="srv.py", start_line=1)
    db.upsert_node(n1)

    with patch("vibetrace.mcp.server._get_context_for_path", return_value=(db, tmp_path)):
        # Impact analysis JSON
        impact_out = await vibetrace_impact_analysis(
            ImpactAnalysisInput(file_path="srv.py", response_format=ResponseFormat.JSON)
        )
        impact_data = json.loads(impact_out)
        assert "file_path" in impact_data
        assert "total_impact" in impact_data
        assert "levels" in impact_data

        # Dependencies JSON
        dep_out = await vibetrace_get_dependencies(
            DependenciesInput(file_path="srv.py", response_format=ResponseFormat.JSON)
        )
        dep_data = json.loads(dep_out)
        assert "file_path" in dep_data
        assert "counts" in dep_data
        assert "internal" in dep_data

    db.close()


def test_watcher_blocking_closes_db(tmp_path: Path):
    """Verify start_watcher_blocking ensures db.close() is called on exit."""
    db_path = tmp_path / "test_watch.db"
    db = IndexerDB(str(db_path))

    closed = False
    orig_close = db.close

    def mock_close():
        nonlocal closed
        closed = True
        orig_close()

    db.close = mock_close

    with (
        patch("vibetrace.services.graph_service.get_context_for_path", return_value=(db, tmp_path)),
        patch("vibetrace.indexer.watcher.start_observer") as mock_obs,
        patch("time.sleep", side_effect=KeyboardInterrupt),
    ):
        start_watcher_blocking(str(tmp_path))
        assert mock_obs.return_value is not None
        assert closed is True
