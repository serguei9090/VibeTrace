"""Tests for architectural split detection, code hashing, TOML baseline, and triage audit."""

from __future__ import annotations

import argparse
from pathlib import Path

import pytest
from starlette.testclient import TestClient

from vibetrace.cli_audit import cmd_audit_splits
from vibetrace.indexer.db import IndexerDB
from vibetrace.mcp.schemas import ApproveSplitInput, AuditSplitsInput, ResponseFormat
from vibetrace.mcp.server import vibetrace_approve_split, vibetrace_audit_splits
from vibetrace.server_api import app
from vibetrace.services.split_audit_service import (
    audit_splits,
    compute_symbol_code_hash,
    load_split_baseline,
    record_split_approval,
    save_split_baseline,
)


@pytest.fixture
def temp_project(tmp_path: Path):
    """Fixture providing a mock project directory structure."""
    src_dir = tmp_path / "src"
    src_dir.mkdir(parents=True)
    sample_file = src_dir / "service.py"
    sample_file.write_text(
        "def dispatch(event):\n    # handler logic\n    call_a()\n    call_b()\n",
        encoding="utf-8",
    )
    return tmp_path


def test_compute_symbol_code_hash(temp_project: Path):
    file_rel = "src/service.py"
    # Compute initial hash
    h1 = compute_symbol_code_hash(file_rel, 1, 4, temp_project)
    assert len(h1) == 64

    # Whitespace indentation/empty line change should not alter hash
    sample_file = temp_project / "src" / "service.py"
    sample_file.write_text(
        "\n\ndef dispatch(event):\n\n    # handler logic\n    call_a()\n    call_b()\n\n",
        encoding="utf-8",
    )
    h2 = compute_symbol_code_hash(file_rel, 1, 8, temp_project)
    assert h1 == h2

    # Actual code change should alter hash
    sample_file.write_text(
        "def dispatch(event):\n    call_c()\n",
        encoding="utf-8",
    )
    h3 = compute_symbol_code_hash(file_rel, 1, 2, temp_project)
    assert h1 != h3


def test_baseline_save_and_load(temp_project: Path):
    splits = [
        {
            "symbol": "router_dispatch",
            "qualified_name": "AppRouter.dispatch",
            "file_path": "src/router.py",
            "status": "approved",
            "code_hash": "a" * 64,
            "branches_count": 3,
            "branches": ["handler_a", "handler_b", "handler_c"],
            "rationale": "Verified event bus routing",
            "reviewed_by": "lead_dev",
            "reviewed_at": "2026-10-30T00:00:00Z",
        }
    ]

    toml_path = save_split_baseline(temp_project, splits)
    assert toml_path.is_file()

    loaded = load_split_baseline(temp_project)
    assert "router_dispatch" in loaded
    assert "AppRouter.dispatch" in loaded
    rec = loaded["router_dispatch"]
    assert rec["code_hash"] == "a" * 64
    assert rec["status"] == "approved"
    assert len(rec["branches"]) == 3


def test_record_split_approval_helper(temp_project: Path):
    record_split_approval(
        project_root=temp_project,
        symbol="process_item",
        qualified_name="Worker.process_item",
        file_path="src/worker.py",
        code_hash="b" * 64,
        branches=["storage_save", "metrics_emit"],
        status="approved",
        rationale="Unit verified",
    )

    baseline = load_split_baseline(temp_project)
    assert "process_item" in baseline
    assert baseline["process_item"]["code_hash"] == "b" * 64
    assert baseline["process_item"]["status"] == "approved"


def test_split_audit_lifecycle(temp_project: Path):
    db_file = temp_project / ".vibetrace.db"
    db = IndexerDB(str(db_file))

    # Seed nodes and edges representing a cross-module split
    with db._get_conn() as conn:
        conn.execute(
            """
            INSERT INTO nodes (id, name, qualified_name, kind, file_path, start_line, end_line)
            VALUES
            ('fn1', 'handle_request', 'handle_request', 'function', 'src/service.py', 1, 4),
            ('fn2', 'validate_auth', 'validate_auth', 'function', 'src/auth.py', 10, 20),
            ('fn3', 'query_db', 'query_db', 'function', 'src/db.py', 15, 30);
            """
        )
        conn.execute(
            """
            INSERT INTO edges (from_node_id, to_node_id, relation_type, call_line)
            VALUES
            ('fn1', 'fn2', 'calls', 2),
            ('fn1', 'fn3', 'calls', 3);
            """
        )

    # 1. Unapproved initial state
    rep1 = audit_splits(db, temp_project)
    assert rep1.total_splits == 1
    assert rep1.new_unapproved_count == 1
    split = rep1.splits[0]
    assert split.symbol == "handle_request"
    assert split.status == "NEW_UNAPPROVED"
    assert split.branches_count == 2

    # 2. Record approval
    record_split_approval(
        project_root=temp_project,
        symbol=split.symbol,
        qualified_name=split.qualified_name,
        file_path=split.file_path,
        code_hash=split.code_hash,
        branches=[b.target_name for b in split.branches],
        status="approved",
    )

    # 3. Verified state
    rep2 = audit_splits(db, temp_project)
    assert rep2.approved_count == 1
    assert rep2.splits[0].status == "APPROVED"

    # 4. Modify code to trigger MODIFIED_CODE state
    (temp_project / "src" / "service.py").write_text(
        "def handle_request():\n    # modified body\n    return False\n",
        encoding="utf-8",
    )
    rep3 = audit_splits(db, temp_project)
    assert rep3.modified_count == 1
    assert rep3.splits[0].status == "MODIFIED_CODE"


def test_server_api_splits():
    client = TestClient(app)

    # Test audit endpoint
    res = client.get("/api/splits/audit")
    assert res.status_code == 200
    data = res.json()
    assert "total_splits" in data
    assert "splits" in data

    # Test approve endpoint
    approve_payload = {
        "symbol": "test_symbol_approval",
        "qualified_name": "test_symbol_approval",
        "file_path": "src/vibetrace/dummy.py",
        "code_hash": "c" * 64,
        "branches": ["branch1", "branch2"],
        "status": "approved",
        "rationale": "Automated test verification",
        "reviewed_by": "pytest",
    }
    post_res = client.post("/api/splits/approve", json=approve_payload)
    assert post_res.status_code == 200
    assert post_res.json()["status"] == "ok"


def test_cli_audit_splits_json(temp_project: Path):
    args = argparse.Namespace(
        path=str(temp_project),
        root=str(temp_project),
        symbol=None,
        ci=False,
        strict=False,
        json=True,
        no_sync=True,
    )
    code = cmd_audit_splits(args)
    assert code == 0


@pytest.mark.asyncio
async def test_mcp_splits_tools():
    # Test audit tool
    audit_inp = AuditSplitsInput(scope_path=".", response_format=ResponseFormat.JSON)
    audit_res = await vibetrace_audit_splits(audit_inp)
    assert "total_splits" in audit_res

    # Test approve tool
    approve_inp = ApproveSplitInput(
        symbol="mcp_test_split",
        code_hash="d" * 64,
        file_path="src/mcp_test.py",
        branches=["b1", "b2"],
        scope_path=".",
        response_format=ResponseFormat.JSON,
    )
    approve_res = await vibetrace_approve_split(approve_inp)
    assert "status" in approve_res


def test_split_audit_ignores_references_and_duplicate_calls_to_same_target(temp_project: Path):
    """Ensure type references and repeated calls to the same function do not trigger splits."""
    db = IndexerDB(str(temp_project / ".vibetrace_noref.db"))
    with db._get_conn() as conn:
        conn.execute(
            """
            INSERT INTO nodes (id, name, qualified_name, kind, file_path, start_line, end_line)
            VALUES
            ('fn_main', 'process', 'process', 'function', 'src/service.py', 1, 4),
            ('fn_log', 'log_info', 'log_info', 'function', 'src/logger.py', 1, 5),
            ('cls_dto', 'OrderDTO', 'OrderDTO', 'class', 'src/models.py', 1, 10);
            """
        )
        conn.execute(
            """
            INSERT INTO edges (from_node_id, to_node_id, relation_type, call_line)
            VALUES
            ('fn_main', 'fn_log', 'calls', 2),
            ('fn_main', 'fn_log', 'calls', 3),
            ('fn_main', 'cls_dto', 'references', 1);
            """
        )
    rep = audit_splits(db, temp_project)
    assert rep.total_splits == 0
