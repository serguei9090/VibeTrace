"""Tests for SQLFluff adapter."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock, patch

from vibetrace.quality.adapters.sql_fluff import SqlFluffAdapter
from vibetrace.quality.models import QualityCategory, QualitySeverity


def test_sqlfluff_adapter_parse_output(tmp_path: Path):
    sample_json = json.dumps(
        [
            {
                "filepath": "queries/users.sql",
                "violations": [
                    {
                        "line_no": 1,
                        "line_pos": 1,
                        "code": "CP01",
                        "description": "Keywords must be consistently upper case.",
                        "name": "capitalisation.keywords",
                    },
                    {
                        "line_no": 5,
                        "line_pos": 1,
                        "code": "ST06",
                        "description": "Select wildcards then other columns.",
                        "name": "structure.column_order",
                    },
                    {
                        "line_no": 10,
                        "line_pos": 1,
                        "code": "AM04",
                        "description": "Query produces an un-named column.",
                        "name": "ambiguous.column_naming",
                    },
                ],
            }
        ]
    )
    adapter = SqlFluffAdapter()
    findings = adapter.parse_sqlfluff_output(sample_json, tmp_path)
    assert len(findings) == 3

    st06 = next(f for f in findings if f.rule_id == "sql:S106")
    assert st06.severity == QualitySeverity.MEDIUM
    assert st06.category == QualityCategory.MAINTAINABILITY
    assert st06.rspec_id == "RSPEC-106"

    cp01 = next(f for f in findings if f.rule_id == "sql:S104")
    assert cp01.severity == QualitySeverity.LOW
    assert cp01.category == QualityCategory.CODE_SMELL

    am04 = next(f for f in findings if f.rule_id == "sql:S3776")
    assert am04.category == QualityCategory.COMPLEXITY


@patch("subprocess.run")
def test_sqlfluff_adapter_availability(mock_run: MagicMock, tmp_path: Path):
    mock_run.return_value = MagicMock(returncode=0, stdout="sqlfluff, version 3.0.0\n")
    adapter = SqlFluffAdapter()
    with patch("shutil.which", return_value="/usr/bin/sqlfluff"):
        assert adapter.is_available(tmp_path)
