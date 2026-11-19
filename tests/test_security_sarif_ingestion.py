"""Tests for Semgrep and universal SARIF security ingestion."""

import json
from pathlib import Path
from unittest.mock import patch

from vibetrace.indexer.db import IndexerDB
from vibetrace.indexer.models import Node
from vibetrace.security.adapters.semgrep import SemgrepAdapter
from vibetrace.security.models import FindingCategory, SecurityFinding, Severity
from vibetrace.security.sarif_ingest import SarifIngestEngine
from vibetrace.security.service import SecurityService


def test_semgrep_sarif_ingest_cwe78(tmp_path: Path) -> None:
    sarif_data = {
        "$schema": "https://raw.githubusercontent.com/oasis-tcs/sarif-spec/master/Schemata/sarif-schema-2.1.0.json",
        "version": "2.1.0",
        "runs": [
            {
                "tool": {
                    "driver": {
                        "name": "semgrep",
                        "rules": [
                            {
                                "id": "python.lang.security.audit.subprocess-shell-true",
                                "shortDescription": {"text": "subprocess call with shell=True"},
                                "properties": {"cwe": ["CWE-78: OS Command Injection"]},
                            }
                        ],
                    }
                },
                "results": [
                    {
                        "ruleId": "python.lang.security.audit.subprocess-shell-true",
                        "level": "error",
                        "message": {"text": "Found subprocess call with shell=True."},
                        "locations": [
                            {
                                "physicalLocation": {
                                    "artifactLocation": {"uri": "src/backup/runner.py"},
                                    "region": {"startLine": 52, "endLine": 52},
                                }
                            }
                        ],
                    }
                ],
            }
        ],
    }

    sarif_file = tmp_path / "semgrep.sarif"
    sarif_file.write_text(json.dumps(sarif_data), encoding="utf-8")

    findings = SarifIngestEngine.parse_sarif_file(sarif_file, tmp_path)
    assert len(findings) == 1
    f = findings[0]
    assert f.file_path == "src/backup/runner.py"
    assert f.start_line == 52
    assert f.cwe_id == "CWE-78"
    assert f.category == FindingCategory.AST_PATTERN
    assert f.severity == Severity.HIGH
    assert "subprocess" in f.message


def test_semgrep_sarif_ingest_cwe_from_tags_and_deduplication(tmp_path: Path) -> None:
    """Verifies SARIF CWE extraction from properties.tags and Semgrep deduplication."""
    sarif_data = {
        "version": "2.1.0",
        "runs": [
            {
                "tool": {
                    "driver": {
                        "name": "semgrep",
                        "rules": [
                            {
                                "id": "python.security.eval-exec",
                                "properties": {"tags": ["CWE-95", "security"]},
                            },
                            {
                                "id": "codeql.command-injection",
                                "properties": {"tags": ["external/cwe/cwe-078"]},
                            },
                        ],
                    }
                },
                "results": [
                    {
                        "ruleId": "python.security.eval-exec",
                        "level": "error",
                        "message": {"text": "Dynamic eval() detected."},
                        "locations": [
                            {
                                "physicalLocation": {
                                    "artifactLocation": {"uri": "app.py"},
                                    "region": {"startLine": 1, "endLine": 1},
                                }
                            }
                        ],
                    },
                    {
                        "ruleId": "codeql.command-injection",
                        "level": "error",
                        "message": {"text": "Command injection."},
                        "locations": [
                            {
                                "physicalLocation": {
                                    "artifactLocation": {"uri": "app.py"},
                                    "region": {"startLine": 2, "endLine": 2},
                                }
                            }
                        ],
                    },
                ],
            }
        ],
    }

    parsed = SarifIngestEngine.parse_sarif_dict(sarif_data, tmp_path)
    assert [f.cwe_id for f in parsed] == ["CWE-95", "CWE-78"]

    # Overlapping CWE-95 on line 1 is deduplicated; novel CWE-295 on line 2 is kept
    (tmp_path / "app.py").write_text("eval('2+2')\nx = 1\n", encoding="utf-8")
    ctx = tmp_path / ".vibetrace"
    ctx.mkdir(parents=True, exist_ok=True)
    db = IndexerDB(str(ctx / "vibetrace.db"))
    db.upsert_node(
        Node(
            id="a1",
            name="app_fn",
            qualified_name="app_fn",
            kind="function",
            file_path="app.py",
            start_line=1,
            end_line=2,
        )
    )

    overlapping = SecurityFinding(
        id="SARIF-SEMG-overlap95",
        rule_id="semgrep oss:python.security.eval-exec",
        cwe_id="CWE-95",
        cwe_ids=["CWE-95"],
        category=FindingCategory.AST_PATTERN,
        severity=Severity.HIGH,
        title="Eval",
        message="Dynamic eval",
        file_path="app.py",
        start_line=1,
        end_line=1,
        code_hash="hash95",
        metadata={"scanner": "semgrep"},
    )
    novel = SecurityFinding(
        id="SARIF-SEMG-novel295",
        rule_id="semgrep oss:python.security.requests-tls-verify-disabled",
        cwe_id="CWE-295",
        cwe_ids=["CWE-295"],
        category=FindingCategory.AST_PATTERN,
        severity=Severity.HIGH,
        title="TLS",
        message="Disabled TLS verification",
        file_path="app.py",
        start_line=2,
        end_line=2,
        code_hash="hash295",
        metadata={"scanner": "semgrep"},
    )

    with (
        patch.object(SemgrepAdapter, "is_available", return_value=True),
        patch.object(SemgrepAdapter, "scan_project", return_value=[overlapping, novel]),
    ):
        report = SecurityService.audit_project(db, tmp_path, check_dependencies=False)

    cwe_95 = [f for f in report.findings if f.file_path == "app.py" and f.start_line == 1]
    cwe_295 = [f for f in report.findings if f.cwe_id == "CWE-295"]
    assert len(cwe_95) == 1
    assert cwe_95[0].id != "SARIF-SEMG-overlap95"
    assert len(cwe_295) == 1
    assert cwe_295[0].id == "SARIF-SEMG-novel295"
