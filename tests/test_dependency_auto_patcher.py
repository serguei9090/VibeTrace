"""Tests for Automated Dependency Auto-Patcher and Reachability Triage."""

import argparse
from pathlib import Path

from vibetrace.cli_audit import cmd_audit_fix_deps
from vibetrace.security.auto_patcher import (
    DependencyAutoPatcher,
    check_package_reachability,
)
from vibetrace.security.baseline import SecurityBaselineManager
from vibetrace.security.models import FindingCategory, SecurityFinding, Severity


def _create_mock_finding(
    pkg_name: str,
    pkg_version: str,
    cve_id: str,
    ecosystem: str = "PyPI",
    fixed_version: str | None = None,
    severity: Severity = Severity.HIGH,
) -> SecurityFinding:
    return SecurityFinding(
        id=f"SEC-{cve_id}-{pkg_name}-{pkg_version}",
        rule_id=f"{ecosystem.lower()}:{cve_id}",
        cwe_id="CWE-1395",
        category=FindingCategory.PACKAGE_CVE,
        severity=severity,
        title=f"Vulnerable Package: {pkg_name}@{pkg_version} ({cve_id})",
        message=f"Flaw in {pkg_name}",
        file_path="uv.lock" if ecosystem == "PyPI" else "package.json",
        start_line=1,
        end_line=1,
        symbol_name=pkg_name,
        code_hash=f"cve:{ecosystem}:{pkg_name}:{pkg_version}",
        metadata={
            "cve_id": cve_id,
            "ecosystem": ecosystem,
            "fixed_version": fixed_version,
            "package_name": pkg_name,
        },
    )


def test_auto_patch_upgrades_known_fixed_versions(tmp_path: Path):
    findings = [
        _create_mock_finding("anyio", "4.10.0", "CVE-2025-0001", fixed_version="4.12.0"),
        _create_mock_finding("anyio", "4.10.0", "CVE-2025-0002", fixed_version="4.14.2"),
    ]

    report = DependencyAutoPatcher.build_patch_plan(tmp_path, findings)
    assert report.total_vulnerable_packages == 1
    assert report.upgradable_count == 1
    action = report.actions[0]
    assert action.package_name == "anyio"
    assert action.action_type == "UPGRADE"
    assert action.fixed_version == "4.14.2"
    assert "uv lock --upgrade-package anyio" in (action.proposed_command or "")


def test_auto_patch_ecosystem_commands(tmp_path: Path):
    findings = [
        _create_mock_finding(
            "axios", "1.7.0", "CVE-2025-1000", ecosystem="npm", fixed_version="1.7.4"
        ),
        _create_mock_finding(
            "serde", "1.0.0", "CVE-2025-2000", ecosystem="Cargo", fixed_version="1.0.210"
        ),
        _create_mock_finding(
            "golang.org/x/crypto",
            "0.20.0",
            "CVE-2025-3000",
            ecosystem="Go",
            fixed_version="0.25.0",
        ),
    ]

    report = DependencyAutoPatcher.build_patch_plan(tmp_path, findings)
    commands = {a.package_name: a.proposed_command for a in report.actions}
    assert commands["axios"] == "bun update axios"
    assert commands["serde"] == "cargo update -p serde"
    assert commands["golang.org/x/crypto"] == "go get golang.org/x/crypto@v0.25.0"


def test_ast_reachability_uncalled_vs_actively_called(tmp_path: Path):
    # Setup test workspace
    src_dir = tmp_path / "src"
    src_dir.mkdir(parents=True)

    app_file = src_dir / "app.py"
    app_file.write_text(
        "import active_lib\n\ndef run():\n    return active_lib.do_work()\n",
        encoding="utf-8",
    )

    # 1. Test actively called package
    callers, ref_files = check_package_reachability(tmp_path, "active_lib")
    assert callers >= 1
    assert any("app.py" in f for f in ref_files)

    # 2. Test uncalled package
    callers_orphan, ref_files_orphan = check_package_reachability(tmp_path, "orphan_lib")
    assert callers_orphan == 0
    assert len(ref_files_orphan) == 0


def test_unpatched_dependency_triage(tmp_path: Path):
    src_dir = tmp_path / "src"
    src_dir.mkdir(parents=True)
    app_file = src_dir / "app.py"
    app_file.write_text("from reachable_zero_day import exploit_me\n", encoding="utf-8")

    findings = [
        # Zero-day with NO fixed version, but actively imported
        _create_mock_finding("reachable_zero_day", "1.0.0", "CVE-2026-9999", fixed_version=None),
        # Unpatched package with ZERO imports in application code
        _create_mock_finding("unreachable_zero_day", "2.1.0", "CVE-2026-8888", fixed_version=None),
    ]

    report = DependencyAutoPatcher.build_patch_plan(tmp_path, findings)
    assert report.unpatched_reachable_count == 1
    assert report.unpatched_unreachable_count == 1

    act_map = {a.package_name: a for a in report.actions}
    assert act_map["reachable_zero_day"].action_type == "UNPATCHED_REACHABLE"
    assert act_map["reachable_zero_day"].reachability_callers >= 1

    assert act_map["unreachable_zero_day"].action_type == "UNPATCHED_UNREACHABLE"
    assert act_map["unreachable_zero_day"].reachability_callers == 0


def test_suppress_unpatched_dependency_in_baseline(tmp_path: Path):
    findings = [
        _create_mock_finding("dead_dep", "1.0.0", "CVE-2026-7777", fixed_version=None),
    ]

    report = DependencyAutoPatcher.build_patch_plan(tmp_path, findings)
    assert report.actions[0].action_type == "UNPATCHED_UNREACHABLE"

    # Apply with suppress_unpatched=True
    updated = DependencyAutoPatcher.apply_patch_plan(tmp_path, report, suppress_unpatched=True)
    assert updated.suppressed_count == 1
    assert updated.actions[0].status == "SUPPRESSED"

    # Verify written to .vibetrace/security.toml
    baseline = SecurityBaselineManager.load_baseline(tmp_path)
    assert len(baseline) >= 1
    assert any("dead_dep" in k for k in baseline)


def test_cli_audit_fix_deps_dry_run_and_json(tmp_path: Path):
    (tmp_path / "pyproject.toml").write_text(
        "[project]\nname='test'\nversion='0.1.0'\n",
        encoding="utf-8",
    )
    args = argparse.Namespace(
        path=str(tmp_path),
        package=None,
        apply=False,
        suppress_unpatched=False,
        json=True,
        no_sync=True,
    )
    code = cmd_audit_fix_deps(args)
    assert code == 0
