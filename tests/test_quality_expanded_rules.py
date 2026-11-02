"""Unit tests for expanded Python quality rules and regex maintainability smells."""

from __future__ import annotations

from pathlib import Path

from vibetrace.quality.rules import resolve_canonical_rule_id
from vibetrace.quality.service import QualityService


def test_taxonomy_resolves_all_new_sonar_rule_aliases() -> None:
    assert resolve_canonical_rule_id("ERA001") == "S125"
    assert resolve_canonical_rule_id("ARG001") == "S1172"
    assert resolve_canonical_rule_id("A001") == "S5720"
    assert resolve_canonical_rule_id("TRY302") == "S5754"
    assert resolve_canonical_rule_id("duplicated-literal") == "S1192"
    assert resolve_canonical_rule_id("dead-function") == "S1144"


def test_quality_service_surfaces_regex_maintainability_smells(tmp_path: Path) -> None:
    src_dir = tmp_path / "src"
    src_dir.mkdir(parents=True)
    sample_file = src_dir / "regex_sample.py"
    sample_file.write_text(
        'import re\n_WORD_RE = re.compile(r"[A-Za-z0-9_]+")\n',
        encoding="utf-8",
    )

    report = QualityService.audit_project(
        db=None,
        project_root=tmp_path,
        complexity_threshold=15,
        include_tests=False,
    )

    regex_smells = [f for f in report.findings if f.rule_id == "python:S6353"]
    assert len(regex_smells) == 1
    assert "concise character class syntax '\\w'" in regex_smells[0].title
    assert regex_smells[0].category.value == "code_smell"
