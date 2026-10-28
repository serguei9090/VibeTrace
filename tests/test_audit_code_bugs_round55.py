"""Tests for Audit Round 55: Web UI Integration Contracts (Caller Guard & Vibe Bundle)."""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from vibetrace.indexer.db import IndexerDB
from vibetrace.indexer.models import Edge, Node
from vibetrace.server_api import app


@pytest.fixture
def web_ui_db(tmp_path: Path) -> tuple[IndexerDB, Path]:
    """Create a temporary populated DB matching Web UI expectations."""
    db_file = tmp_path / ".vibetrace" / "vibetrace.db"
    db_file.parent.mkdir(parents=True, exist_ok=True)
    db = IndexerDB(db_path=str(db_file))

    # Source symbol
    src_file = tmp_path / "src" / "api.ts"
    src_file.parent.mkdir(parents=True, exist_ok=True)
    src_file.write_text(
        "export function fetchData(): Promise<any> {\n    return fetch('/data');\n}\n",
        encoding="utf-8",
    )

    node_target = Node(
        id="api:fetchData",
        name="fetchData",
        qualified_name="api.fetchData",
        kind="function",
        file_path="src/api.ts",
        start_line=1,
        end_line=3,
        signature="(): Promise<any>",
    )
    db.upsert_node(node_target)

    # Caller symbol
    client_file = tmp_path / "src" / "client.ts"
    client_file.write_text(
        "import { fetchData } from './api';\nfunction load() {\n    const d = fetchData();\n}\n",
        encoding="utf-8",
    )

    node_caller = Node(
        id="client:load",
        name="load",
        qualified_name="client.load",
        kind="function",
        file_path="src/client.ts",
        start_line=2,
        end_line=4,
    )
    db.upsert_node(node_caller)
    db.upsert_edge(
        Edge(
            from_node_id="client:load",
            to_node_id="api:fetchData",
            relation_type="calls",
            call_line=3,
        )
    )

    return db, tmp_path


def test_web_ui_caller_guard_contract(
    web_ui_db: tuple[IndexerDB, Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    """Verify CallerGuardBadge data contracts against backend API."""
    db, root = web_ui_db
    monkeypatch.setattr("vibetrace.server_api.db", db)
    monkeypatch.setenv("VIBETRACE_PROJECT_ROOT", str(root))

    client = TestClient(app)
    res = client.get("/api/vibe/guard?symbol=fetchData&file_path=src/api.ts")
    assert res.status_code == 200

    data = res.json()
    assert data["found"] is True
    assert data["symbol"] == "fetchData"
    assert data["file_path"] == "src/api.ts"
    assert data["total_callers"] == 1
    assert len(data["callers"]) == 1
    caller = data["callers"][0]
    assert caller["caller_name"] == "load"
    assert caller["caller_file"] == "src/client.ts"
    assert caller["call_line"] == 3
    assert "fetchData" in caller["call_snippet"]
    assert "GUARD ALERT" in data["safety_advisory"]


def test_web_ui_vibe_bundle_contract(
    web_ui_db: tuple[IndexerDB, Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    """Verify VibeBundleButton data contracts against backend API."""
    db, root = web_ui_db
    monkeypatch.setattr("vibetrace.server_api.db", db)
    monkeypatch.setenv("VIBETRACE_PROJECT_ROOT", str(root))

    client = TestClient(app)
    res = client.get("/api/vibe/bundle?intent=fetchData&symbol=fetchData&budget=1500")
    assert res.status_code == 200

    data = res.json()
    assert data["intent"] == "fetchData"
    assert data["target_symbol"] == "fetchData"
    assert data["token_budget"] == 1500
    assert data["estimated_tokens"] > 0
    assert "formatted_prompt" in data
    assert "# Vibe Bundle: fetchData" in data["formatted_prompt"]
    assert "```typescript" in data["formatted_prompt"]
