"""Tests for RuffAdapter and Sonar RSPEC rule mapping."""

from pathlib import Path

from vibetrace.quality.adapters.python_ruff import RuffAdapter
from vibetrace.quality.models import QualityCategory


def test_ruff_adapter_detects_and_maps_sonar_rules(tmp_path: Path) -> None:
    adapter = RuffAdapter()
    if not adapter.is_available(tmp_path):
        return  # Skip in environments without ruff installed

    # Code violating flake8-bugbear B006 (Sonar S5708: mutable default argument)
    # and flake8-simplify SIM102 (Sonar S1066: mergeable nested if)
    py_code = """
def bad_function(items=[]):
    if True:
        if len(items) > 0:
            return items[0]
    return None
"""
    test_file = tmp_path / "smelly.py"
    test_file.write_text(py_code, encoding="utf-8")

    findings = adapter.audit_file(test_file, tmp_path)
    rule_ids = {f.rule_id for f in findings}

    # B006 maps to python:S5714 (RSPEC-5714: Default argument values should not be mutable)
    assert any("S5714" in r or "B006" in r for r in rule_ids), f"Missing B006/S5714 in {rule_ids}"

    # Verify finding metadata
    b006_finding = next((f for f in findings if "S5714" in f.rule_id or "B006" in f.rule_id), None)
    assert b006_finding is not None
    assert b006_finding.category == QualityCategory.RELIABILITY
    assert b006_finding.file_path == "smelly.py"
    assert b006_finding.start_line == 2


def test_ruff_adapter_audit_project_batch(tmp_path: Path) -> None:
    adapter = RuffAdapter()
    if not adapter.is_available(tmp_path):
        return

    f1 = tmp_path / "clean.py"
    f1.write_text("def clean_fn(x: int) -> int:\n    return x + 1\n", encoding="utf-8")

    f2 = tmp_path / "dirty.py"
    f2.write_text("def dirty_fn(a, b=[]):\n    return a\n", encoding="utf-8")

    findings = adapter.audit_project(tmp_path, [f1, f2])
    assert any("dirty.py" in f.file_path for f in findings)
    assert not any("clean.py" in f.file_path for f in findings)
