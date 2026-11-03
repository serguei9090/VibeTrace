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


def test_quality_service_surfaces_s2612_file_permissions(tmp_path: Path) -> None:
    src_dir = tmp_path / "src"
    src_dir.mkdir(parents=True)
    sample_file = src_dir / "chmod_sample.py"
    sample_file.write_text(
        "import os\nfrom pathlib import Path\n"
        "os.chmod('file.txt', 0o755)\n"
        "Path('script.sh').chmod(0o600)\n",
        encoding="utf-8",
    )

    report = QualityService.audit_project(
        db=None,
        project_root=tmp_path,
        complexity_threshold=15,
        include_tests=False,
    )

    perm_smells = [f for f in report.findings if f.rule_id == "python:S2612"]
    assert len(perm_smells) == 1
    assert perm_smells[0].start_line == 3
    assert "0o755" in perm_smells[0].message
    assert perm_smells[0].status == "UNAPPROVED"


def test_quality_service_surfaces_s7503_async_without_await(tmp_path: Path) -> None:
    src_dir = tmp_path / "src"
    src_dir.mkdir(parents=True)
    sample_file = src_dir / "async_sample.py"
    sample_file.write_text(
        "import asyncio\n\n"
        "async def bad_sync_fn(x: int) -> int:\n"
        "    return x * 2\n\n"
        "async def good_async_fn(x: int) -> int:\n"
        "    await asyncio.sleep(0.01)\n"
        "    return x * 2\n\n"
        "async def stub_protocol() -> None:\n"
        "    ...\n",
        encoding="utf-8",
    )

    report = QualityService.audit_project(
        db=None,
        project_root=tmp_path,
        complexity_threshold=15,
        include_tests=False,
    )

    async_smells = [f for f in report.findings if f.rule_id == "python:S7503"]
    assert len(async_smells) == 1
    assert async_smells[0].symbol_name == "bad_sync_fn"
    assert async_smells[0].start_line == 3
    assert "Use asynchronous features in this function" in async_smells[0].title


def test_quality_sarif_export(tmp_path: Path) -> None:
    import json

    from vibetrace.quality.formatters import format_quality_audit_sarif

    src_dir = tmp_path / "src"
    src_dir.mkdir(parents=True)
    sample_file = src_dir / "sarif_sample.py"
    sample_file.write_text(
        "import os\nos.chmod('file.txt', 0o777)\n",
        encoding="utf-8",
    )

    report = QualityService.audit_project(
        db=None,
        project_root=tmp_path,
        complexity_threshold=15,
        include_tests=False,
    )

    sarif_str = format_quality_audit_sarif(report)
    sarif_data = json.loads(sarif_str)

    assert sarif_data["version"] == "2.1.0"
    assert len(sarif_data["runs"]) == 1
    run = sarif_data["runs"][0]
    assert run["tool"]["driver"]["name"] == "VibeTrace-Quality"
    assert len(run["results"]) >= 1
    res = run["results"][0]
    assert "ruleId" in res
    assert "locations" in res


def test_treesitter_polyglot_s134_and_s1192(tmp_path: Path) -> None:
    src_dir = tmp_path / "src"
    src_dir.mkdir(parents=True)
    ts_file = src_dir / "polyglot_sample.ts"
    # S134: 4 nested control structures (depth 4 > 3)
    # S1192: literal "unauthorized_user_access" repeated 3 times
    ts_content = """function processUser(a: number, b: number) {
    const msg1 = "unauthorized_user_access";
    if (a > 0) {
        if (b > 0) {
            for (let i = 0; i < 10; i++) {
                if (i === 5) {
                    console.log("unauthorized_user_access");
                }
            }
        }
    }
    return "unauthorized_user_access";
}
"""
    ts_file.write_text(ts_content, encoding="utf-8")

    report = QualityService.audit_project(
        db=None,
        project_root=tmp_path,
        complexity_threshold=15,
        include_tests=False,
    )

    s134_findings = [f for f in report.findings if f.rule_id == "typescript:S134"]
    assert len(s134_findings) >= 1
    assert "nested too deeply" in s134_findings[0].title

    s1192_findings = [f for f in report.findings if f.rule_id == "typescript:S1192"]
    assert len(s1192_findings) == 1
    assert "unauthorized_user_access" in s1192_findings[0].title


def test_quality_fix_project_s7503_codemod(tmp_path: Path) -> None:
    src_dir = tmp_path / "src"
    src_dir.mkdir(parents=True)
    sample_file = src_dir / "async_sync_codemod.py"
    sample_file.write_text(
        "async def compute_total(val: int) -> int:\n    return val * 10\n",
        encoding="utf-8",
    )

    # 1. Audit before fix -> 1 S7503 finding
    report_before = QualityService.audit_project(
        db=None,
        project_root=tmp_path,
        complexity_threshold=15,
        include_tests=False,
    )
    assert any(f.rule_id == "python:S7503" for f in report_before.findings)

    # 2. Run fix_project
    fix_res = QualityService.fix_project(project_root=tmp_path)
    assert fix_res.get("codemods", {}).get("python:S7503") == 1

    # 3. File was converted to sync def
    content_after = sample_file.read_text(encoding="utf-8")
    assert "def compute_total(val: int) -> int:" in content_after
    assert "async def compute_total" not in content_after

    # 4. Audit after fix -> 0 S7503 findings
    report_after = QualityService.audit_project(
        db=None,
        project_root=tmp_path,
        complexity_threshold=15,
        include_tests=False,
    )
    assert not any(f.rule_id == "python:S7503" for f in report_after.findings)
