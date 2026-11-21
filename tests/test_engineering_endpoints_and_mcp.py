"""Verification tests for Transitive Dependencies, Refactoring, and Docs in REST API & MCP."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from starlette.testclient import TestClient

from vibetrace.indexer.db import IndexerDB
from vibetrace.indexer.main import reindex_all
from vibetrace.mcp.schemas import (
    AddDocCatalogInput,
    ApproveQualityExceptionInput,
    AuditPRTaintInput,
    AuditQualityInput,
    DeadCodeInput,
    DocCatalogsInput,
    ExplainDependencyInput,
    IndexDocsInput,
    RemoveDocCatalogInput,
    ResponseFormat,
)
from vibetrace.mcp.server import (
    vibetrace_add_doc_catalog,
    vibetrace_approve_quality_exception,
    vibetrace_audit_quality,
    vibetrace_index_docs,
    vibetrace_list_doc_catalogs,
    vibetrace_remove_doc_catalog,
)
from vibetrace.mcp.tools_engineering import (
    vibetrace_audit_pr_taint,
    vibetrace_dead_code,
    vibetrace_explain_dependency,
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


async def test_mcp_quality_audit_and_approve(tmp_path: Path) -> None:
    (tmp_path / "complex.py").write_text(
        "def nested_work(items: list[int]) -> int:\n"
        "    total = 0\n"
        "    for x in items:\n"
        "        if x > 0:\n"
        "            if x % 2 == 0:\n"
        "                total += x\n"
        "    return total\n",
        encoding="utf-8",
    )
    vt_dir = tmp_path / ".vibetrace"
    vt_dir.mkdir(exist_ok=True)
    db = IndexerDB(str(vt_dir / "vibetrace.db"))
    try:
        reindex_all(db, str(tmp_path), project_root=tmp_path)
    finally:
        db.close()

    res = await vibetrace_audit_quality(
        AuditQualityInput(
            threshold=1,
            scan_all=True,
            scope_path=str(tmp_path),
            response_format=ResponseFormat.JSON,
        )
    )
    report = json.loads(res)
    assert report["total_findings"] >= 1
    first = report["findings"][0]

    approve_res = await vibetrace_approve_quality_exception(
        ApproveQualityExceptionInput(
            finding_id=first["id"],
            rule_id=first["rule_id"],
            file_path=first["file_path"],
            code_hash=first["code_hash"],
            rationale="Verified acceptable",
            symbol_name=first.get("symbol_name"),
            scope_path=str(tmp_path),
            response_format=ResponseFormat.JSON,
        )
    )
    approve_data = json.loads(approve_res)
    assert approve_data["status"] == "ok"
    assert (vt_dir / "quality.toml").is_file()


def test_mcp_and_rest_dead_code(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    (tmp_path / "app.py").write_text(
        "def _used_helper() -> int:\n    return 1\n\n"
        "def _unused_helper() -> int:\n    return 2\n\n"
        "def main() -> int:\n    return _used_helper()\n",
        encoding="utf-8",
    )
    vt_dir = tmp_path / ".vibetrace"
    vt_dir.mkdir(exist_ok=True)
    db = IndexerDB(str(vt_dir / "vibetrace.db"))
    try:
        reindex_all(db, str(tmp_path), project_root=tmp_path)

        mcp_res = vibetrace_dead_code(
            DeadCodeInput(
                scope_path=str(tmp_path),
                response_format=ResponseFormat.JSON,
            )
        )
        mcp_data = json.loads(mcp_res)
        assert mcp_data["total"] >= 1
        assert any(sym["name"] == "_unused_helper" for sym in mcp_data["symbols"])

        monkeypatch.setattr("vibetrace.server_api.db", db)
        monkeypatch.setenv("VIBETRACE_PROJECT_ROOT", str(tmp_path))
        rest_client = TestClient(app)
        resp = rest_client.get("/api/dead-code")
        assert resp.status_code == 200
        rest_data = resp.json()
        assert any(sym["name"] == "_unused_helper" for sym in rest_data["symbols"])
    finally:
        db.close()


def test_mcp_audit_pr_taint(tmp_path: Path) -> None:
    (tmp_path / "app.py").write_text("def ok() -> int:\n    return 1\n", encoding="utf-8")
    vt_dir = tmp_path / ".vibetrace"
    vt_dir.mkdir(exist_ok=True)
    db = IndexerDB(str(vt_dir / "vibetrace.db"))
    try:
        reindex_all(db, str(tmp_path), project_root=tmp_path)
    finally:
        db.close()

    res = vibetrace_audit_pr_taint(
        AuditPRTaintInput(
            staged=True,
            scope_path=str(tmp_path),
            response_format=ResponseFormat.JSON,
        )
    )
    data = json.loads(res)
    assert "passed_gate" in data
    assert "total_findings" in data


def test_rest_security_audit_writes_security_report_domain(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    (tmp_path / "vuln.py").write_text(
        "import subprocess\n\ndef run_cmd(user_arg: str) -> None:\n"
        "    subprocess.run(user_arg, shell=True)\n",
        encoding="utf-8",
    )
    vt_dir = tmp_path / ".vibetrace"
    vt_dir.mkdir(exist_ok=True)
    db = IndexerDB(str(vt_dir / "vibetrace.db"))
    try:
        reindex_all(db, str(tmp_path), project_root=tmp_path)
        monkeypatch.setattr("vibetrace.server_api.db", db)
        monkeypatch.setattr("vibetrace.server_api.project_root", str(tmp_path))
        monkeypatch.setenv("VIBETRACE_PROJECT_ROOT", str(tmp_path))

        rest_client = TestClient(app)
        resp = rest_client.get(
            "/api/security/audit?skip_deps=true&skip_semgrep=true&skip_trivy=true&scan_all=true"
        )
        assert resp.status_code == 200
        assert (vt_dir / "reports" / "security").exists()
        assert not (vt_dir / "reports" / "quality").exists()
    finally:
        db.close()


async def test_mcp_doc_catalog_add_index_remove(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("VIBETRACE_GLOBAL_ROOT", str(tmp_path / "global_vt"))
    proj = tmp_path / "proj"
    docs_dir = proj / "docs"
    (proj / ".vibetrace").mkdir(parents=True)
    docs_dir.mkdir(parents=True)
    (docs_dir / "guide.md").write_text(
        "# Auth Flow\n\nJWT verification runs in middleware.\n",
        encoding="utf-8",
    )

    add_res = await vibetrace_add_doc_catalog(
        AddDocCatalogInput(
            name="guides",
            path="docs",
            scope_path=str(proj),
            response_format=ResponseFormat.JSON,
        )
    )
    add_data = json.loads(add_res)
    assert add_data["status"] == "ok"
    assert add_data["catalog"] == "guides"

    idx_res = await vibetrace_index_docs(
        IndexDocsInput(
            catalog="guides",
            scope_path=str(proj),
            response_format=ResponseFormat.JSON,
        )
    )
    idx_data = json.loads(idx_res)
    assert idx_data["status"] == "ok"
    assert idx_data["total_files"] >= 1
    assert idx_data["total_chunks"] >= 1

    list_res = await vibetrace_list_doc_catalogs(
        DocCatalogsInput(scope_path=str(proj), response_format=ResponseFormat.JSON)
    )
    cats = json.loads(list_res)
    assert any(c["name"] == "guides" for c in cats)

    rem_res = await vibetrace_remove_doc_catalog(
        RemoveDocCatalogInput(
            name="guides",
            scope_path=str(proj),
            response_format=ResponseFormat.JSON,
        )
    )
    rem_data = json.loads(rem_res)
    assert rem_data["removed"] is True
