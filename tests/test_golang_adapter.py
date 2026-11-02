"""Unit tests for Go golangci-lint adapter and Sonar RSPEC translation."""

from __future__ import annotations

from pathlib import Path

from vibetrace.quality.adapters.golang_lint import GolangLintAdapter
from vibetrace.quality.models import QualityCategory, QualitySeverity


def test_golang_adapter_issue_mapping(tmp_path: Path) -> None:
    """Verifies mapping of golangci-lint JSON issues to Sonar RSPEC findings."""
    adapter = GolangLintAdapter()

    # 1. gocyclo complexity issue
    gocyclo_issue = {
        "FromLinter": "gocyclo",
        "Text": "cyclomatic complexity 18 of func `Compute` is high (> 15)",
        "Severity": "warning",
        "Pos": {
            "Filename": "pkg/calc/compute.go",
            "Line": 45,
            "Column": 1,
        },
    }

    f1 = adapter._map_issue_to_finding(gocyclo_issue, tmp_path)
    assert f1 is not None
    assert f1.rspec_id == "RSPEC-3776"
    assert f1.category == QualityCategory.COMPLEXITY
    assert f1.severity == QualitySeverity.HIGH
    assert f1.file_path == "pkg/calc/compute.go"
    assert f1.start_line == 45

    # 2. errcheck unhandled error issue
    errcheck_issue = {
        "FromLinter": "errcheck",
        "Text": "Error return value of `os.Remove` is not checked",
        "Severity": "error",
        "Pos": {
            "Filename": "main.go",
            "Line": 12,
            "Column": 5,
        },
    }

    f2 = adapter._map_issue_to_finding(errcheck_issue, tmp_path)
    assert f2 is not None
    assert f2.rspec_id == "RSPEC-2970"
    assert f2.category == QualityCategory.RELIABILITY
    assert f2.severity == QualitySeverity.HIGH


def test_golang_adapter_audit_project_filtering(tmp_path: Path, monkeypatch) -> None:
    """Verifies audit_project filters issues by target file list."""
    adapter = GolangLintAdapter()

    issues = [
        {
            "FromLinter": "ineffassign",
            "Text": "ineffectual assignment to err",
            "Pos": {"Filename": "handler.go", "Line": 25},
        },
        {
            "FromLinter": "unused",
            "Text": "func `helper` is unused",
            "Pos": {"Filename": "unused.go", "Line": 80},
        },
    ]

    monkeypatch.setattr(adapter, "_run_golangci_lint", lambda root, args: issues)

    # Filter to only handler.go
    findings = adapter.audit_project(tmp_path, [tmp_path / "handler.go"])
    assert len(findings) == 1
    assert findings[0].file_path == "handler.go"
    assert findings[0].rspec_id == "RSPEC-1854"


def test_golang_adapter_availability_detection(tmp_path: Path) -> None:
    """Verifies is_available checks runner and go.mod presence."""
    adapter = GolangLintAdapter()
    assert adapter.is_available(tmp_path) is False

    go_mod = tmp_path / "go.mod"
    go_mod.write_text("module example.com/test\n\ngo 1.22\n", encoding="utf-8")

    if adapter._find_runner(tmp_path):
        assert adapter.is_available(tmp_path) is True
