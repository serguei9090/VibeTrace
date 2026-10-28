"""Tests for Audit Round 56: Git Automation (Pre-Commit Caller Guard Check)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from vibetrace.cli import main
from vibetrace.indexer.db import IndexerDB
from vibetrace.indexer.models import Edge, Node
from vibetrace.services.caller_guard import (
    CallerGuardService,
    format_staged_guard_md,
    parse_git_diff_line_ranges,
)


def test_parse_git_diff_line_ranges() -> None:
    diff_sample = """
diff --git a/src/billing.py b/src/billing.py
index abc..def 100644
--- a/src/billing.py
+++ b/src/billing.py
@@ -10,3 +10,5 @@
+def new_logic():
+    pass
@@ -45 +47,2 @@
-old_call()
+new_call()
diff --git a/src/utils.ts b/src/utils.ts
--- a/src/utils.ts
+++ b/src/utils.ts
@@ -100,5 +100,8 @@
"""
    ranges = parse_git_diff_line_ranges(diff_sample)
    assert "src/billing.py" in ranges
    assert (10, 14) in ranges["src/billing.py"]
    assert (47, 48) in ranges["src/billing.py"]
    assert "src/utils.ts" in ranges
    assert (100, 107) in ranges["src/utils.ts"]


@pytest.fixture
def staged_db(tmp_path: Path) -> tuple[IndexerDB, Path]:
    db_file = tmp_path / ".vibetrace" / "vibetrace.db"
    db_file.parent.mkdir(parents=True, exist_ok=True)
    db = IndexerDB(db_path=str(db_file))

    # Node 1: calculate_tax in src/billing.py (L10-L20), has 1 caller
    billing_file = tmp_path / "src" / "billing.py"
    billing_file.parent.mkdir(parents=True, exist_ok=True)
    billing_file.write_text(
        "# Header\n" * 9 + "def calculate_tax(amount: float) -> float:\n    return amount * 0.2\n",
        encoding="utf-8",
    )
    node_tax = Node(
        id="billing:calculate_tax",
        name="calculate_tax",
        qualified_name="calculate_tax",
        kind="function",
        file_path="src/billing.py",
        start_line=10,
        end_line=20,
    )
    db.upsert_node(node_tax)

    # Node 2: safe_helper in src/billing.py (L40-L50), 0 callers
    node_safe = Node(
        id="billing:safe_helper",
        name="safe_helper",
        qualified_name="safe_helper",
        kind="function",
        file_path="src/billing.py",
        start_line=40,
        end_line=50,
    )
    db.upsert_node(node_safe)

    # Caller to calculate_tax
    checkout_file = tmp_path / "src" / "checkout.py"
    checkout_file.write_text("def checkout(): calculate_tax(50)\n", encoding="utf-8")
    node_caller = Node(
        id="checkout:checkout",
        name="checkout",
        qualified_name="checkout",
        kind="function",
        file_path="src/checkout.py",
        start_line=1,
        end_line=1,
    )
    db.upsert_node(node_caller)
    db.upsert_edge(
        Edge(
            from_node_id="checkout:checkout",
            to_node_id="billing:calculate_tax",
            relation_type="calls",
            call_line=1,
        )
    )

    return db, tmp_path


def test_staged_caller_guard_detection(staged_db: tuple[IndexerDB, Path]) -> None:
    db, root = staged_db

    # Diff modifying lines 10-12 in src/billing.py (overlaps with calculate_tax L10-11)
    mock_diff = """
diff --git a/src/billing.py b/src/billing.py
--- a/src/billing.py
+++ b/src/billing.py
@@ -10,2 +10,3 @@
"""
    reports = CallerGuardService.get_staged_caller_guard(db, root, diff_text=mock_diff)
    assert len(reports) == 1
    report = reports[0]
    assert report.symbol == "calculate_tax"
    assert report.total_callers == 1
    assert report.callers[0].caller_name == "checkout"

    md = format_staged_guard_md(reports)
    assert "Pre-Commit Caller Guard Check" in md
    assert "⚠️ `calculate_tax`" in md
    assert "checkout" in md


def test_staged_caller_guard_empty_diff(staged_db: tuple[IndexerDB, Path]) -> None:
    db, root = staged_db
    reports = CallerGuardService.get_staged_caller_guard(db, root, diff_text="")
    assert len(reports) == 0
    md = format_staged_guard_md(reports)
    assert "No modified symbol definitions detected" in md


def test_staged_caller_guard_cli(
    staged_db: tuple[IndexerDB, Path],
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _, root = staged_db

    mock_diff = """
diff --git a/src/billing.py b/src/billing.py
--- a/src/billing.py
+++ b/src/billing.py
@@ -10,2 +10,3 @@
"""
    orig_fn = CallerGuardService.get_staged_caller_guard
    monkeypatch.setattr(
        "vibetrace.services.caller_guard.CallerGuardService.get_staged_caller_guard",
        lambda _db, _root, **_kwargs: orig_fn(_db, _root, diff_text=mock_diff),
    )

    # 1. Advisory mode (exit 0 even if callers exist)
    code = main(["guard", "--staged", "--no-sync", "--root", str(root)])
    assert code == 0
    out, _ = capsys.readouterr()
    assert "Pre-Commit Caller Guard Check" in out
    assert "calculate_tax" in out
    assert "checkout" in out

    # 2. JSON mode
    code_json = main(["guard", "--staged", "--json", "--no-sync", "--root", str(root)])
    assert code_json == 0
    out_json, _ = capsys.readouterr()
    data = json.loads(out_json)
    assert len(data) == 1
    assert data[0]["symbol"] == "calculate_tax"
    assert data[0]["total_callers"] == 1

    # 3. Strict mode (exit 1 if callers affected)
    code_strict = main(["guard", "--staged", "--strict", "--no-sync", "--root", str(root)])
    assert code_strict == 1
