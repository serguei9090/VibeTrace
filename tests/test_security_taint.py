"""Tests for call-graph taint reachability escalation."""

from pathlib import Path
from typing import Literal

from vibetrace.indexer.db import IndexerDB
from vibetrace.indexer.models import Edge, Node
from vibetrace.security.baseline import SecurityBaselineManager
from vibetrace.security.models import FindingCategory, Severity
from vibetrace.security.service import SecurityService

SRC = "import os\ndef sink(x):\n    os.system(x)\n\ndef handler(req):\n    sink(req)\n"


def _node(
    nid: str,
    name: str,
    start: int,
    end: int,
    kind: Literal["function", "endpoint"] = "function",
    decorators=None,
) -> Node:
    return Node(
        id=nid,
        name=name,
        qualified_name=name,
        kind=kind,
        file_path="app.py",
        start_line=start,
        end_line=end,
        decorators=decorators,
    )


def _setup(tmp_path: Path, handler_decorators, extra_edges=()) -> IndexerDB:
    (tmp_path / "app.py").write_text(SRC, encoding="utf-8")
    db = IndexerDB(str(tmp_path / "t.db"))
    db.upsert_node(_node("sink", "sink", 2, 3))
    db.upsert_node(_node("handler", "handler", 5, 6, decorators=handler_decorators))
    db.upsert_edge(Edge(from_node_id="handler", to_node_id="sink", relation_type="calls"))
    for e in extra_edges:
        db.upsert_edge(e)
    return db


def test_reachable_sink_is_escalated_with_path(tmp_path: Path):
    db = _setup(tmp_path, ['@app.post("/run")'])
    report = SecurityService.audit_project(db, tmp_path, check_dependencies=False)
    f = next(x for x in report.findings if x.cwe_id == "CWE-78")
    assert f.category == FindingCategory.TAINT_REACHABILITY
    assert f.metadata["taint_path"] == ["handler", "sink"]
    assert f.symbol_name == "sink"


def test_unreachable_sink_not_escalated(tmp_path: Path):
    db = _setup(tmp_path, None)
    report = SecurityService.audit_project(db, tmp_path, check_dependencies=False)
    f = next(x for x in report.findings if x.cwe_id == "CWE-78")
    assert f.category == FindingCategory.AST_PATTERN
    assert "taint_path" not in f.metadata


def test_sanitizer_on_path_blocks_escalation(tmp_path: Path):
    db = _setup(tmp_path, None)
    db.upsert_node(_node("san", "quote", 8, 9))
    db.upsert_node(_node("ep", "entry", 10, 11, kind="endpoint"))
    db.upsert_edge(Edge(from_node_id="san", to_node_id="sink", relation_type="calls"))
    db.upsert_edge(Edge(from_node_id="ep", to_node_id="san", relation_type="calls"))
    report = SecurityService.audit_project(db, tmp_path, check_dependencies=False)
    f = next(x for x in report.findings if x.cwe_id == "CWE-78")
    assert f.category == FindingCategory.AST_PATTERN


def test_non_critical_severity_is_bumped_one_tier():
    from vibetrace.security.taint_engine import _bump

    assert _bump(Severity.MEDIUM) == Severity.HIGH
    assert _bump(Severity.CRITICAL) == Severity.CRITICAL


def test_custom_sanitizer_from_toml_and_no_taint_flag(tmp_path: Path):
    db = _setup(tmp_path, None)
    db.upsert_node(_node("san", "my_clean", 8, 9))
    db.upsert_node(_node("ep", "entry", 10, 11, kind="endpoint"))
    db.upsert_edge(Edge(from_node_id="san", to_node_id="sink", relation_type="calls"))
    db.upsert_edge(Edge(from_node_id="ep", to_node_id="san", relation_type="calls"))
    (tmp_path / ".vibetrace").mkdir()
    (tmp_path / ".vibetrace" / "security.toml").write_text(
        '[taint]\nsanitizers = ["my_clean"]\n', encoding="utf-8"
    )
    report = SecurityService.audit_project(db, tmp_path, check_dependencies=False)
    f = next(x for x in report.findings if x.cwe_id == "CWE-78")
    assert f.category == FindingCategory.AST_PATTERN

    # sanitizers survive a baseline rewrite
    SecurityService.record_exception(tmp_path, f.id, f.rule_id, f.file_path, f.code_hash, "ok")
    assert SecurityBaselineManager.load_sanitizers(tmp_path) == ["my_clean"]


def test_no_taint_flag_disables_escalation(tmp_path: Path):
    db = _setup(tmp_path, ['@app.post("/run")'])
    report = SecurityService.audit_project(
        db, tmp_path, check_dependencies=False, analyze_taint=False
    )
    f = next(x for x in report.findings if x.cwe_id == "CWE-78")
    assert f.category == FindingCategory.AST_PATTERN
