"""Unit and integration tests for Biome Quality Sub-Engine Adapter."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

from vibetrace.quality.adapters.biome import BIOME_TO_SONAR, BiomeAdapter
from vibetrace.quality.models import QualityCategory, QualitySeverity
from vibetrace.quality.service import QualityService


def test_biome_availability() -> None:
    adapter = BiomeAdapter()
    # Confirms runner detection returns a boolean without raising
    assert isinstance(adapter.is_available(Path.cwd()), bool)


def test_biome_maps_sonar_rules() -> None:
    assert "lint/security/noGlobalEval" in BIOME_TO_SONAR
    s_code, rspec, cat, sev = BIOME_TO_SONAR["lint/security/noGlobalEval"]
    assert s_code == "S1523"
    assert rspec == "RSPEC-1523"
    assert cat == QualityCategory.RELIABILITY
    assert sev == QualitySeverity.HIGH

    s_code, rspec, cat, sev = BIOME_TO_SONAR["lint/suspicious/noDoubleEquals"]
    assert s_code == "S1444"
    assert rspec == "RSPEC-1444"
    assert cat == QualityCategory.CODE_SMELL


def test_biome_audit_single_file(tmp_path: Path) -> None:
    adapter = BiomeAdapter()
    if not adapter.is_available(tmp_path):
        return

    ts_file = tmp_path / "sample.ts"
    ts_file.write_text(
        "const x: any = 1;\nif (x == 1) {\n    eval('alert(1)');\n}\n",
        encoding="utf-8",
    )

    findings = adapter.audit_file(ts_file, tmp_path)
    assert len(findings) >= 1

    rule_ids = {f.rule_id for f in findings}
    rspec_ids = {f.rspec_id for f in findings}

    # At least eval or double equals should be reported
    assert (
        "biome:lint/security/noGlobalEval" in rule_ids
        or "biome:lint/suspicious/noDoubleEquals" in rule_ids
    )
    assert "RSPEC-1523" in rspec_ids or "RSPEC-1444" in rspec_ids


def test_biome_batch_project_audit(tmp_path: Path) -> None:
    adapter = BiomeAdapter()
    if not adapter.is_available(tmp_path):
        return

    file1 = tmp_path / "a.ts"
    file1.write_text("eval('danger');\n", encoding="utf-8")

    file2 = tmp_path / "b.tsx"
    file2.write_text("const a = 1; if (a == 2) {}\n", encoding="utf-8")

    findings = adapter.audit_project(tmp_path, [file1, file2])
    assert len(findings) >= 1
    file_paths = {f.file_path for f in findings}
    assert "a.ts" in file_paths or "b.tsx" in file_paths


def test_biome_graceful_fallback_when_unavailable(tmp_path: Path) -> None:
    adapter = BiomeAdapter()
    with patch.object(adapter, "_find_runner", return_value=None):
        assert adapter.is_available(tmp_path) is False
        assert adapter.audit_file(tmp_path / "test.ts", tmp_path) == []
        assert adapter.audit_project(tmp_path, [tmp_path / "test.ts"]) == []


def test_quality_service_integrates_biome(tmp_path: Path) -> None:
    ts_file = tmp_path / "service_test.ts"
    ts_file.write_text("eval('untrusted');\n", encoding="utf-8")

    report = QualityService.audit_project(
        db=None,
        project_root=tmp_path,
        file_list=["service_test.ts"],
    )

    # Findings should include the Biome finding if available
    adapter = BiomeAdapter()
    if adapter.is_available(tmp_path):
        assert report.total_findings >= 1
        f = report.findings[0]
        assert "biome" in f.rule_id
        assert f.rspec_id == "RSPEC-1523"
        assert f.file_path == "service_test.ts"
