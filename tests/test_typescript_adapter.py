"""Unit tests for TypeScript incremental compiler (tsc) sub-engine adapter."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

from vibetrace.quality.adapters.typescript_tsc import TSC_TO_SONAR, TypeScriptAdapter
from vibetrace.quality.models import QualityCategory, QualitySeverity


def test_typescript_adapter_availability() -> None:
    adapter = TypeScriptAdapter()
    root = Path.cwd()
    assert adapter.is_available(root) is True


def test_tsc_maps_deprecated_and_type_rules() -> None:
    assert "6385" in TSC_TO_SONAR
    s_code, rspec, cat, sev = TSC_TO_SONAR["6385"]
    assert s_code == "S1874"
    assert rspec == "RSPEC-1874"
    assert cat == QualityCategory.CODE_SMELL

    assert "2322" in TSC_TO_SONAR
    s_code, rspec, cat, sev = TSC_TO_SONAR["2322"]
    assert s_code == "S4335"
    assert rspec == "RSPEC-4335"
    assert cat == QualityCategory.RELIABILITY
    assert sev == QualitySeverity.HIGH


def test_tsc_output_parser(tmp_path: Path) -> None:
    adapter = TypeScriptAdapter()
    sample_output = (
        "src/web/App.tsx(14,5): error TS6385: 'legacyProp' is deprecated.\n"
        "src/web/main.tsx(22,10): error TS2322: Type 'string' is not assignable to type 'number'.\n"
        "src/web/main.tsx(30,1): warning TS7006: Parameter 'x' implicitly has an 'any' type.\n"
    )

    findings = adapter._parse_tsc_output(sample_output, tmp_path)
    assert len(findings) == 3

    # Finding 1: TS6385 -> S1874
    f1 = findings[0]
    assert f1.file_path == "src/web/App.tsx"
    assert f1.start_line == 14
    assert f1.rspec_id == "RSPEC-1874"
    assert "[S1874]" in f1.title
    assert "legacyProp" in f1.message

    # Finding 2: TS2322 -> S4335
    f2 = findings[1]
    assert f2.file_path == "src/web/main.tsx"
    assert f2.start_line == 22
    assert f2.rspec_id == "RSPEC-4335"
    assert f2.severity == QualitySeverity.HIGH

    # Finding 3: TS7006 -> S6548
    f3 = findings[2]
    assert f3.file_path == "src/web/main.tsx"
    assert f3.start_line == 30
    assert f3.rspec_id == "RSPEC-6548"


def test_tsc_graceful_when_no_runner(tmp_path: Path) -> None:
    adapter = TypeScriptAdapter()
    with patch.object(adapter, "_find_runner", return_value=None):
        assert adapter.is_available(tmp_path) is False
        assert adapter.audit_project(tmp_path) == []
