"""Unit tests for review gate typecheck flag integration."""

from __future__ import annotations

import argparse
from pathlib import Path
from unittest.mock import MagicMock

from vibetrace.cli_audit import cmd_review
from vibetrace.indexer.db import IndexerDB
from vibetrace.quality.service import QualityService
from vibetrace.services.review_service import run_unified_review


def test_run_unified_review_typecheck_forwarding(tmp_path: Path, monkeypatch) -> None:
    """Verifies run_unified_review forwards typecheck=True to QualityService.audit_project."""
    db_mock = MagicMock(spec=IndexerDB)
    typecheck_received = None

    orig_audit = QualityService.audit_project

    def mock_audit(*args, **kwargs):
        nonlocal typecheck_received
        typecheck_received = kwargs.get("typecheck")
        return orig_audit(*args, **kwargs)

    monkeypatch.setattr(QualityService, "audit_project", mock_audit)

    rep = run_unified_review(
        db=db_mock,
        project_root=tmp_path,
        scope="full",
        check_dependencies=False,
        typecheck=True,
    )

    assert rep is not None
    assert typecheck_received is True


def test_cmd_review_typecheck_flag(tmp_path: Path, monkeypatch) -> None:
    """Verifies cmd_review extracts --typecheck and passes it to run_unified_review."""
    review_args = {}

    def mock_run_review(**kwargs):
        nonlocal review_args
        review_args = kwargs
        from vibetrace.services.review_service import UnifiedReviewReport

        return UnifiedReviewReport(
            passed=True,
            scope="full",
        )

    monkeypatch.setattr(
        "vibetrace.services.review_service.run_unified_review",
        mock_run_review,
    )

    args = argparse.Namespace(
        path=str(tmp_path),
        full=True,
        deps=False,
        min_coverage=None,
        strict=False,
        sarif=None,
        typecheck=True,
        ci=False,
        json=True,
        no_sync=True,
        root=str(tmp_path),
    )

    code = cmd_review(args)
    assert code == 0
    assert review_args.get("typecheck") is True
