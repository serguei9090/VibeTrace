"""Unit tests for automated safe auto-fixer (QualityService.fix_project, Ruff & Biome adapters)."""

from __future__ import annotations

import argparse
from pathlib import Path

from vibetrace.cli_audit import cmd_audit_quality
from vibetrace.quality.adapters.biome import BiomeAdapter
from vibetrace.quality.adapters.python_ruff import RuffAdapter
from vibetrace.quality.service import QualityService


def test_ruff_adapter_fix_files(tmp_path: Path) -> None:
    """Verifies RuffAdapter fix_files executes and safely formats python files."""
    adapter = RuffAdapter()
    if not adapter.is_available(tmp_path):
        return

    py_file = tmp_path / "fixable.py"
    # An unused import that ruff check --fix removes, or simple fixable formatting
    py_file.write_text("import sys\n\ndef sample(  ): \n    return 42\n", encoding="utf-8")

    res = adapter.fix_files(tmp_path, [py_file])
    assert res["available"] is True
    assert res["success"] is True
    assert res["files_count"] == 1


def test_biome_adapter_fix_files(tmp_path: Path) -> None:
    """Verifies BiomeAdapter fix_files executes and safely formats JS/TS files."""
    adapter = BiomeAdapter()
    if not adapter.is_available(tmp_path):
        return

    ts_file = tmp_path / "fixable.ts"
    # Code with unformatted indentation and double equals
    ts_file.write_text("const x: number = 10;\nconst y = 20;\n", encoding="utf-8")

    res = adapter.fix_files(tmp_path, [ts_file])
    assert res["available"] is True
    assert res["success"] is True
    assert res["files_count"] == 1


def test_quality_service_fix_project(tmp_path: Path) -> None:
    """Verifies QualityService.fix_project batches both Python and Web file fixes."""
    py_file = tmp_path / "app.py"
    py_file.write_text("def run():\n    return True\n", encoding="utf-8")

    ts_file = tmp_path / "app.ts"
    ts_file.write_text("export const run = (): boolean => true;\n", encoding="utf-8")

    res = QualityService.fix_project(tmp_path)
    assert res["total_files"] >= 2
    assert "engines" in res


def test_cmd_audit_quality_with_fix(tmp_path: Path, monkeypatch) -> None:
    """Verifies cmd_audit_quality invokes fix_project when --fix is provided."""
    fixed_called = False

    def fake_fix(project_root, file_filter=None, file_list=None, include_tests=False):
        nonlocal fixed_called
        fixed_called = True
        return {"engines": {"ruff": {"success": True}}, "total_files": 1}

    monkeypatch.setattr(QualityService, "fix_project", fake_fix)

    args = argparse.Namespace(
        path=str(tmp_path),
        file=None,
        fix=True,
        threshold=15,
        include_tests=False,
        typecheck=False,
        approve=False,
        json=False,
        sarif=False,
        ci=False,
        strict=False,
        fail_on="HIGH",
        no_sync=True,
        root=str(tmp_path),
    )

    exit_code = cmd_audit_quality(args)
    assert exit_code == 0
    assert fixed_called is True
