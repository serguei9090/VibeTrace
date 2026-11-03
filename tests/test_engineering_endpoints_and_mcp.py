"""Verification tests for Transitive Dependencies, Refactoring, and Docs in REST API & MCP."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from starlette.testclient import TestClient

from vibetrace.mcp.schemas import (
    ExplainDependencyInput,
    RefactorMethodInput,
    ResponseFormat,
)
from vibetrace.mcp.tools_engineering import (
    vibetrace_explain_dependency,
    vibetrace_refactor_method,
)
from vibetrace.server_api import app


@pytest.fixture
def client() -> TestClient:
    return TestClient(app)


def test_rest_api_deps_endpoints(client: TestClient) -> None:
    # 1. /api/deps/why
    resp = client.get("/api/deps/why?package=fastapi")
    assert resp.status_code == 200
    data = resp.json()
    assert data["target"] == "fastapi"
    assert "chains" in data

    # 2. /api/deps/tree
    resp_tree = client.get("/api/deps/tree?max_depth=2")
    assert resp_tree.status_code == 200
    tree_data = resp_tree.json()
    assert "tree" in tree_data

    # 3. /api/deps/conflicts
    resp_conflicts = client.get("/api/deps/conflicts")
    assert resp_conflicts.status_code == 200
    conflicts_data = resp_conflicts.json()
    assert "conflicts" in conflicts_data


def test_rest_api_docs_endpoints(client: TestClient) -> None:
    # 1. /api/docs/check
    resp = client.get("/api/docs/check")
    assert resp.status_code in (200, 400)  # 200 if index exists, 400 if index missing

    # 2. /api/docs/sync-links
    resp_sync = client.post("/api/docs/sync-links")
    assert resp_sync.status_code in (200, 400)


def test_mcp_explain_dependency() -> None:
    # Action 'why'
    res = vibetrace_explain_dependency(
        ExplainDependencyInput(
            package_name="fastapi", action="why", response_format=ResponseFormat.JSON
        )
    )
    data = json.loads(res)
    assert data["target"] == "fastapi"

    # Action 'conflicts'
    res_conflicts = vibetrace_explain_dependency(
        ExplainDependencyInput(action="conflicts", response_format=ResponseFormat.JSON)
    )
    conflicts_data = json.loads(res_conflicts)
    assert "total_conflicts" in conflicts_data

    # Action 'tree'
    res_tree = vibetrace_explain_dependency(
        ExplainDependencyInput(action="tree", max_depth=2, response_format=ResponseFormat.MARKDOWN)
    )
    assert "Transitive Dependency Tree" in res_tree


def test_mcp_refactor_method_candidates(tmp_path: Path) -> None:
    test_file = tmp_path / "sample.py"
    test_file.write_text(
        "def compute(x):\n"
        "    a = x * 2\n"
        "    b = a + 5\n"
        "    if b > 10:\n"
        "        print('high')\n"
        "    return b\n",
        encoding="utf-8",
    )

    res = vibetrace_refactor_method(
        RefactorMethodInput(
            file_path=str(test_file),
            symbol_name="compute",
            scope_path=str(tmp_path),
            response_format=ResponseFormat.JSON,
        )
    )
    candidates = json.loads(res)
    assert isinstance(candidates, list)
