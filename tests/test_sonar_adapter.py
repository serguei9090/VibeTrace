"""Unit and integration tests for the optional Tier-2 SonarLint Headless Java Adapter."""

from __future__ import annotations

import os
from pathlib import Path
from unittest.mock import patch

from vibetrace.quality.adapters.sonar_lint import (
    SonarLintAdapter,
    extract_rspec_short,
    resolve_sonar_runtime,
)
from vibetrace.quality.models import QualityCategory, QualityFinding, QualitySeverity
from vibetrace.quality.service import QualityService


def test_extract_rspec_short() -> None:
    assert extract_rspec_short("python:S3776") == "S3776"
    assert extract_rspec_short("php:S1144") == "S1144"
    assert extract_rspec_short("RSPEC-8786") == "S8786"
    assert extract_rspec_short("S107") == "S107"


def test_resolve_sonar_runtime_priority(tmp_path: Path) -> None:
    """Verifies discovery prefers `~/.vibetrace/tools/sonar` and falls back to IDE extensions."""
    java_name = "java.exe" if os.name == "nt" else "java"

    # 1. Empty home -> None
    assert resolve_sonar_runtime(custom_home=tmp_path) is None

    # 2. Populate `.vscode/extensions/sonarsource.sonarlint-vscode-6.2.0`
    vs_ext = tmp_path / ".vscode" / "extensions" / "sonarsource.sonarlint-vscode-6.2.0"
    (vs_ext / "server").mkdir(parents=True)
    (vs_ext / "server" / "sonarlint-ls.jar").write_bytes(b"jar")
    (vs_ext / "analyzers").mkdir(parents=True)
    (vs_ext / "analyzers" / "sonarpython.jar").write_bytes(b"jar")
    (vs_ext / "jre" / "21" / "bin").mkdir(parents=True)
    (vs_ext / "jre" / "21" / "bin" / java_name).write_bytes(b"bin")

    rt_vscode = resolve_sonar_runtime(custom_home=tmp_path)
    assert rt_vscode is not None
    assert rt_vscode.source_kind == "vscode"
    assert len(rt_vscode.analyzer_jars) == 1
    assert rt_vscode.work_dir == tmp_path / ".vibetrace" / "tools" / "sonar" / "work"

    # 3. Populate `~/.vibetrace/tools/sonar` -> takes precedence over vscode
    vt_sonar = tmp_path / ".vibetrace" / "tools" / "sonar"
    (vt_sonar / "server").mkdir(parents=True)
    (vt_sonar / "server" / "sonarlint-ls.jar").write_bytes(b"jar")
    (vt_sonar / "analyzers").mkdir(parents=True)
    (vt_sonar / "analyzers" / "sonarpython.jar").write_bytes(b"jar")
    (vt_sonar / "analyzers" / "sonarphp.jar").write_bytes(b"jar")
    (vt_sonar / "jre" / "21" / "bin").mkdir(parents=True)
    (vt_sonar / "jre" / "21" / "bin" / java_name).write_bytes(b"bin")

    rt_vt = resolve_sonar_runtime(custom_home=tmp_path)
    assert rt_vt is not None
    assert rt_vt.source_kind == "vibetrace"
    assert len(rt_vt.analyzer_jars) == 2


def test_sonar_adapter_diagnostic_mapping(tmp_path: Path) -> None:
    """Verifies LSP publishDiagnostics records map cleanly to QualityFinding and SecurityFinding."""
    src_file = tmp_path / "app.py"
    src_file.write_text(
        "import re\nPATTERN = re.compile(r'([a-z]+)+$')\n",
        encoding="utf-8",
    )

    diag = {
        "range": {
            "start": {"line": 1, "character": 21},
            "end": {"line": 1, "character": 34},
        },
        "severity": 1,
        "code": "python:S8786",
        "source": "sonarlint",
        "message": "Simplify this regular expression to reduce its runtime (backtracking).",
    }

    qf = SonarLintAdapter._map_diagnostic_to_quality_finding(diag, "app.py", tmp_path)
    assert qf.rule_id == "python:S8786"
    assert qf.rspec_id == "RSPEC-8786"
    assert qf.severity == QualitySeverity.HIGH
    assert qf.start_line == 2
    assert qf.file_path == "app.py"
    assert qf.metadata.get("engine") == "sonarlint-ls"

    adapter = SonarLintAdapter()
    with patch.object(adapter, "audit_project", return_value=[qf]):
        sec_findings = adapter.scan_security_findings(tmp_path, target_files=["app.py"])
        assert len(sec_findings) == 1
        assert sec_findings[0].rule_id == "python:S8786"
        assert sec_findings[0].cwe_id == "CWE-1333"


def test_quality_and_security_service_sonar_deduplication(tmp_path: Path) -> None:
    """Confirms native AST and SonarLint findings on same (file, line, RSPEC) do not duplicate."""
    sample_py = tmp_path / "service.py"
    sample_py.write_text(
        "import re\nBAD_RE = re.compile(r'[A-Za-z0-9_]+')\n",
        encoding="utf-8",
    )

    duplicate_sonar_finding = QualityFinding(
        id="QUAL-S6353-sonar123456",
        rule_id="python:S6353",
        rspec_id="RSPEC-6353",
        category=QualityCategory.CODE_SMELL,
        severity=QualitySeverity.LOW,
        title="Regular expression character classes should be preferred",
        message="Use concise character class syntax '\\w' instead of '[A-Za-z0-9_]'.",
        file_path="service.py",
        start_line=2,
        end_line=2,
        code_hash="sonar123456",
        metadata={"engine": "sonarlint-ls"},
    )

    with (
        patch.object(SonarLintAdapter, "is_available", return_value=True),
        patch.object(SonarLintAdapter, "audit_project", return_value=[duplicate_sonar_finding]),
    ):
        report_default = QualityService.audit_project(
            db=None,
            project_root=tmp_path,
            file_filter="service.py",
            enable_sonar=False,
        )
        report_with_sonar = QualityService.audit_project(
            db=None,
            project_root=tmp_path,
            file_filter="service.py",
            enable_sonar=True,
        )
        s6353_default = [f for f in report_default.findings if "S6353" in f.rule_id]
        s6353_sonar = [f for f in report_with_sonar.findings if "S6353" in f.rule_id]
        assert len(s6353_default) == 1
        assert len(s6353_sonar) == 1
