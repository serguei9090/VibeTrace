"""Tests for PyrightAdapter and type error mapping."""

from pathlib import Path

from vibetrace.quality.adapters.python_pyright import PyrightAdapter
from vibetrace.quality.models import QualityCategory


def test_pyright_adapter_mapping_mock() -> None:
    adapter = PyrightAdapter()
    project_root = Path("/dummy/project")

    raw_item = {
        "file": "/dummy/project/src/service.py",
        "severity": "error",
        "message": "Expression of type 'int' cannot be assigned to return type 'str'",
        "range": {
            "start": {"line": 15, "character": 4},
            "end": {"line": 15, "character": 12},
        },
        "rule": "reportGeneralTypeIssues",
    }

    finding = adapter._map_item_to_finding(raw_item, project_root)
    assert finding is not None
    assert finding.file_path == "src/service.py"
    assert finding.start_line == 16
    assert finding.category == QualityCategory.RELIABILITY
    assert "reportGeneralTypeIssues" in finding.rule_id


def test_pyright_adapter_unawaited_coroutine_mapping() -> None:
    adapter = PyrightAdapter()
    project_root = Path("/dummy/project")

    raw_item = {
        "file": "/dummy/project/src/async_handler.py",
        "severity": "warning",
        "message": "Coroutine 'fetch_data' was never awaited",
        "range": {
            "start": {"line": 42, "character": 4},
            "end": {"line": 42, "character": 16},
        },
        "rule": "reportUnusedExpression",
    }

    finding = adapter._map_item_to_finding(raw_item, project_root)
    assert finding is not None
    assert finding.rule_id == "python:S6544"
    assert finding.rspec_id == "RSPEC-6544"
    assert "awaited" in finding.title.lower()
