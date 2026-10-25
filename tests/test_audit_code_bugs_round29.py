"""Unit and integration tests for Round 29 audit fixes:
- CLI 'references' and 'dependencies' subcommands
- REST API /api/impact, /api/slice, /api/references, /api/dependencies, /api/summary endpoints
- Canonical path cleaning in compute_impact_analysis and categorize_file_dependencies
"""

import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from vibetrace.cli import main as cli_main
from vibetrace.indexer.db import IndexerDB, Node
from vibetrace.server_api import app
from vibetrace.services.graph_service import (
    categorize_file_dependencies,
    compute_impact_analysis,
)


@pytest.fixture
def test_db(tmp_path: Path):
    db_file = tmp_path / "test.db"
    db_inst = IndexerDB(str(db_file))
    yield db_inst
    db_inst.close()


def test_path_sanitization_with_redundant_dot_slashes(test_db: IndexerDB):
    test_db.batch_save_file_index(
        file_path="src/utils.py",
        content_hash="hash123",
        nodes=[
            Node(
                id="n1",
                name="helper_func",
                qualified_name="helper_func",
                parent_id=None,
                file_path="src/utils.py",
                kind="function",
                start_line=1,
                end_line=5,
            )
        ],
        edges=[],
    )

    # Redundant ././ prefix should be cleaned and match indexed nodes
    total, _impacts = compute_impact_analysis(test_db, "././src/utils.py")
    assert total == 0
    # Also verify dependencies categorization cleans paths
    internal, third_party, stdlib = categorize_file_dependencies(test_db, "././src/utils.py")
    assert internal == []
    assert third_party == []
    assert stdlib == []


def test_cli_references_and_dependencies(capsys: pytest.CaptureFixture[str]):
    # Test CLI references on existing codebase symbol
    code = cli_main(["references", "upsert_node", "--json"])
    assert code == 0
    captured = capsys.readouterr()
    data = json.loads(captured.out)
    assert data["found"] is True
    assert data["symbol"] == "upsert_node"
    assert data["total_references"] > 0

    # Test CLI references on missing symbol
    code_missing = cli_main(["references", "non_existent_symbol_xyz", "--json"])
    assert code_missing == 1
    captured_missing = capsys.readouterr()
    data_missing = json.loads(captured_missing.out)
    assert data_missing["found"] is False

    # Test CLI dependencies on cli.py
    code_deps = cli_main(["dependencies", "src/vibetrace/cli.py", "--json"])
    assert code_deps == 0
    captured_deps = capsys.readouterr()
    deps_data = json.loads(captured_deps.out)
    assert deps_data["file_path"] == "src/vibetrace/cli.py"
    assert deps_data["counts"]["stdlib"] >= 1


def test_api_impact_endpoint():
    client = TestClient(app)
    # Test existing indexed file
    res = client.get("/api/impact", params={"file_path": "src/vibetrace/cli.py"})
    assert res.status_code == 200
    payload = res.json()
    assert "file_path" in payload
    assert "total_impact" in payload
    assert "levels" in payload

    # Test non-indexed file
    res_404 = client.get("/api/impact", params={"file_path": "src/vibetrace/ghost_file.py"})
    assert res_404.status_code == 404


def test_api_slice_endpoint():
    client = TestClient(app)
    # Test existing symbol
    res = client.get("/api/slice", params={"symbol": "upsert_node"})
    assert res.status_code == 200
    payload = res.json()
    assert payload["found"] is True
    assert "IndexerDB" in payload["qualified_name"] or payload["name"] == "upsert_node"
    assert len(payload["source_code"]) > 0

    # Test non-existent symbol
    res_404 = client.get("/api/slice", params={"symbol": "phantom_symbol_not_found"})
    assert res_404.status_code == 404


def test_api_references_endpoint():
    client = TestClient(app)
    # Test existing symbol
    res = client.get("/api/references", params={"symbol": "upsert_node"})
    assert res.status_code == 200
    payload = res.json()
    assert payload["symbol"] == "upsert_node"
    assert payload["total_references"] > 0
    assert len(payload["targets"]) >= 1

    # Test non-existent symbol
    res_404 = client.get("/api/references", params={"symbol": "completely_unknown_fn"})
    assert res_404.status_code == 404


def test_api_dependencies_endpoint():
    client = TestClient(app)
    # Test existing file
    res = client.get("/api/dependencies", params={"file_path": "src/vibetrace/cli.py"})
    assert res.status_code == 200
    payload = res.json()
    assert payload["file_path"] == "src/vibetrace/cli.py"
    assert "internal" in payload
    assert "stdlib" in payload

    # Test non-indexed file
    res_404 = client.get("/api/dependencies", params={"file_path": "no/such/file.py"})
    assert res_404.status_code == 404


def test_api_summary_endpoint():
    client = TestClient(app)
    # Test existing file
    res = client.get("/api/summary", params={"file_path": "src/vibetrace/cli.py", "limit": 10})
    assert res.status_code == 200
    payload = res.json()
    assert payload["file_path"] == "src/vibetrace/cli.py"
    assert payload["total"] > 0
    assert len(payload["nodes"]) <= 10

    # Test non-indexed file
    res_404 = client.get("/api/summary", params={"file_path": "phantom.py"})
    assert res_404.status_code == 404
