"""Tests for ShellCheck adapter."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock, patch

from vibetrace.quality.adapters.shellcheck import ShellCheckAdapter
from vibetrace.quality.models import QualityCategory, QualitySeverity


def test_shellcheck_adapter_parse_output(tmp_path: Path):
    sample_json = json.dumps(
        [
            {
                "file": "deploy.sh",
                "line": 15,
                "endLine": 15,
                "column": 5,
                "endColumn": 10,
                "level": "warning",
                "code": 2086,
                "message": "Double quote to prevent globbing and word splitting.",
            },
            {
                "file": "build.sh",
                "line": 4,
                "endLine": 4,
                "column": 1,
                "endColumn": 10,
                "level": "style",
                "code": 2181,
                "message": "Check exit code directly with e.g. 'if mycmd;'.",
            },
        ]
    )
    adapter = ShellCheckAdapter()
    findings = adapter.parse_shellcheck_output(sample_json, tmp_path)
    assert len(findings) == 2

    sc2086 = next(f for f in findings if f.rule_id == "shell:S2086")
    assert sc2086.severity == QualitySeverity.HIGH
    assert sc2086.category == QualityCategory.RELIABILITY
    assert sc2086.rspec_id == "RSPEC-2086"

    sc2181 = next(f for f in findings if f.rule_id == "shell:S2181")
    assert sc2181.severity == QualitySeverity.LOW
    assert sc2181.category == QualityCategory.CODE_SMELL


@patch("subprocess.run")
def test_shellcheck_adapter_availability(mock_run: MagicMock, tmp_path: Path):
    mock_run.return_value = MagicMock(returncode=0, stdout="version: 0.9.0\n")
    adapter = ShellCheckAdapter()
    with patch("shutil.which", return_value="/usr/bin/shellcheck"):
        assert adapter.is_available(tmp_path)
