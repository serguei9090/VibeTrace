import pytest
from fastapi.testclient import TestClient

from vibetrace.indexer.db import Edge, IndexerDB, Node
from vibetrace.server_api import app

client = TestClient(app)


@pytest.fixture
def mock_db(monkeypatch, tmp_path):
    """Overrides the global db and project_root in server_api.py to use a temp file DB."""
    db_file = tmp_path / "test_api_vibetrace.db"
    db = IndexerDB(str(db_file))

    (tmp_path / "a.py").write_text(
        "class NodeA:\n    def run(self):\n        return 1\n",
        encoding="utf-8",
    )

    # Add Sample Data
    db.upsert_node(
        Node(
            id="a",
            name="NodeA",
            qualified_name="NodeA",
            kind="class",
            file_path="a.py",
            start_line=1,
            end_line=3,
        )
    )
    db.upsert_node(
        Node(
            id="b",
            name="run",
            qualified_name="NodeA.run",
            kind="function",
            file_path="a.py",
            start_line=2,
            end_line=3,
        )
    )
    db.upsert_edge(Edge(from_node_id="a", to_node_id="b", relation_type="calls", call_line=2))

    # Monkeypatch the 'db' and 'project_root' variables in server_api
    import vibetrace.server_api

    monkeypatch.setattr(vibetrace.server_api, "db", db)
    monkeypatch.setattr(vibetrace.server_api, "project_root", tmp_path)
    return db


def test_get_graph(mock_db):
    response = client.get("/graph")
    assert response.status_code == 200
    data = response.json()
    assert "nodes" in data
    assert "edges" in data
    assert len(data["nodes"]) == 2
    assert data["nodes"][0]["name"] == "NodeA"


def test_get_graph_filtered(mock_db):
    response = client.get("/graph?file_path=a.py")
    assert response.status_code == 200
    data = response.json()
    assert len(data["nodes"]) == 2

    response_empty = client.get("/graph?file_path=missing.py")
    assert len(response_empty.json()["nodes"]) == 0


def test_get_trace(mock_db):
    response = client.get("/trace?from_symbol=NodeA&to_symbol=NodeA.run&max_depth=4")
    assert response.status_code == 200
    data = response.json()
    assert data["found"] is True
    assert len(data["hops"]) == 1
    assert data["hops"][0]["call_line"] == 2


def test_get_source_and_security(mock_db):
    response = client.get("/source?file_path=a.py&start_line=1&end_line=2")
    assert response.status_code == 200
    data = response.json()
    assert "1: class NodeA:" in data["source_code"]

    # Path traversal blocked
    bad = client.get("/source?file_path=../../etc/passwd")
    assert bad.status_code == 400

    # Missing file
    missing = client.get("/source?file_path=nonexistent.py")
    assert missing.status_code == 404


def test_post_agent_focus(mock_db):
    response = client.post(
        "/api/agent-focus",
        json={
            "event_type": "trace_path",
            "node_ids": ["a", "b"],
            "summary": "Tracing NodeA -> NodeA.run",
            "from_symbol": "NodeA",
            "to_symbol": "NodeA.run",
        },
    )
    assert response.status_code == 200
    assert response.json()["status"] == "broadcast"


def test_search_nodes_endpoint(mock_db):
    response = client.get("/api/search?q=NodeA")
    assert response.status_code == 200
    data = response.json()
    assert len(data) >= 1
    assert data[0]["name"] == "NodeA"

    # Filter by kind
    response_kind = client.get("/api/search?q=run&kind=function")
    assert response_kind.status_code == 200
    assert any(n["name"] == "run" for n in response_kind.json())

    # Test class kind filtering
    res_class = client.get("/api/search?q=NodeA&kind=class")
    assert res_class.status_code == 200
    assert len(res_class.json()) >= 1
    assert all(r["kind"] == "class" for r in res_class.json())

    # Non-existent kind returns empty list gracefully
    res_none = client.get("/api/search?q=NodeA&kind=trait")
    assert res_none.status_code == 200
    assert res_none.json() == []


def test_source_endpoint_empty_file(mock_db, tmp_path):
    (tmp_path / "empty_mod.py").write_text("", encoding="utf-8")
    res = client.get("/source?file_path=empty_mod.py&start_line=1&end_line=5")
    assert res.status_code == 200
    data = res.json()
    assert data["source_code"] == ""
    assert data["start_line"] == 0
    assert data["end_line"] == 0


def test_server_api_root_env_override(monkeypatch, tmp_path):
    import os

    from vibetrace.services.graph_service import get_context_for_path

    monkeypatch.setenv("VIBETRACE_PROJECT_ROOT", str(tmp_path))
    root_override = os.environ.get("VIBETRACE_PROJECT_ROOT")
    _, proj_root = get_context_for_path(".", root_override=root_override, auto_sync=False)
    assert proj_root.resolve() == tmp_path.resolve()
