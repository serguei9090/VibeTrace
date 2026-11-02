"""Tests for Semgrep and universal SARIF security ingestion."""

import json
from pathlib import Path

from vibetrace.security.models import FindingCategory, Severity
from vibetrace.security.sarif_ingest import SarifIngestEngine


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
