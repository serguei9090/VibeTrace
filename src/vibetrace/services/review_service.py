"""Unified AI Code Review & Vibecoding Gate combining Security, Split Audit, and Caller Guard."""

from __future__ import annotations

import subprocess
from pathlib import Path

from pydantic import BaseModel, Field

from vibetrace.indexer.db import IndexerDB
from vibetrace.quality.models import QualityFinding
from vibetrace.quality.service import QualityService
from vibetrace.security.models import SecurityFinding
from vibetrace.security.service import SecurityService
from vibetrace.services.caller_guard import (
    CallerGuardReport,
    CallerGuardService,
    parse_git_diff_line_ranges,
)
from vibetrace.services.coverage_service import CoverageGateReport, CoverageService
from vibetrace.services.split_audit_service import ArchitecturalSplit, audit_splits


class UnifiedReviewReport(BaseModel):
    """Combined 1-call review payload for AI coding agents and CI gates."""

    scope: str = "changed"
    passed: bool
    changed_files: list[str] = Field(default_factory=list)
    unapproved_security_count: int = 0
    modified_security_count: int = 0
    total_security_count: int = 0
    unapproved_quality_count: int = 0
    modified_quality_count: int = 0
    total_quality_count: int = 0
    new_splits_count: int = 0
    modified_splits_count: int = 0
    total_splits_count: int = 0
    approved_splits_count: int = 0
    modified_symbols_with_callers: int = 0
    coverage_gate: CoverageGateReport = Field(default_factory=CoverageGateReport)
    security_findings: list[SecurityFinding] = Field(default_factory=list)
    all_security_findings: list[SecurityFinding] = Field(default_factory=list)
    quality_findings: list[QualityFinding] = Field(default_factory=list)
    actionable_splits: list[ArchitecturalSplit] = Field(default_factory=list)
    all_splits: list[ArchitecturalSplit] = Field(default_factory=list)
    caller_guards: list[CallerGuardReport] = Field(default_factory=list)
    actions_required: list[str] = Field(default_factory=list)


def _detect_git_changed_files(project_root: Path) -> tuple[set[str], str]:
    """Returns normalized relative paths of changed/untracked files and unified diff text."""
    changed: set[str] = set()
    diff_text = ""
    try:
        st = subprocess.run(
            ["git", "status", "--porcelain"],
            cwd=str(project_root),
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            check=False,
        )
        for line in st.stdout.splitlines():
            if len(line) > 3:
                raw_path = line[3:].strip().split(" -> ")[-1].strip('"')
                changed.add(raw_path.replace("\\", "/"))

        df = subprocess.run(
            ["git", "diff", "HEAD", "-U0"],
            cwd=str(project_root),
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            check=False,
        )
        diff_text = df.stdout or ""
    except Exception:
        pass
    return changed, diff_text


def run_unified_review(
    db: IndexerDB,
    project_root: Path,
    scope: str = "changed",
    check_dependencies: bool = False,
    min_coverage: float | None = None,
    enforce_coverage: bool = False,
) -> UnifiedReviewReport:
    """Runs Security + IaC + Git Secret + Split Audit + Caller Guard + Coverage Gate."""
    norm_scope = "full" if scope.lower() == "full" else "changed"
    changed_files, diff_text = _detect_git_changed_files(project_root)

    # 1. Security, IaC, Git Secret & Inter-Procedural Taint Audit
    target_files = sorted(changed_files) if norm_scope == "changed" and changed_files else None
    sec_report = SecurityService.audit_project(
        db=db,
        project_root=project_root,
        file_list=target_files,
        check_dependencies=check_dependencies,
        analyze_taint=True,
        git_commit_depth=25 if norm_scope == "full" else 5,
    )
    active_sec: list[SecurityFinding] = []
    for f in sec_report.findings:
        if f.status not in ("UNAPPROVED", "MODIFIED"):
            continue
        fp = f.file_path.replace("\\", "/")
        if (
            norm_scope == "full"
            or f.status == "MODIFIED"
            or f.category.value == "git_secret"
            or fp in changed_files
            or not changed_files
        ):
            active_sec.append(f)

    unapproved_sec = sum(1 for f in active_sec if f.status == "UNAPPROVED")
    modified_sec = sum(1 for f in active_sec if f.status == "MODIFIED")

    # 2. Code Quality & Cognitive Complexity Audit
    qual_report = QualityService.audit_project(
        db=db,
        project_root=project_root,
        file_list=target_files,
        complexity_threshold=15,
        include_tests=False,
    )
    active_qual: list[QualityFinding] = []
    for f in qual_report.findings:
        if f.status in ("UNAPPROVED", "MODIFIED_CODE"):
            active_qual.append(f)

    unapproved_qual = sum(1 for f in active_qual if f.status == "UNAPPROVED")
    modified_qual = sum(1 for f in active_qual if f.status == "MODIFIED_CODE")

    # 3. Architectural Split Path Audit
    file_ranges = parse_git_diff_line_ranges(diff_text) if diff_text else {}
    split_report = audit_splits(db, project_root)
    active_splits: list[ArchitecturalSplit] = []
    for s in split_report.splits:
        if s.status not in ("NEW_UNAPPROVED", "MODIFIED_CODE", "RISK"):
            continue
        fp = s.file_path.replace("\\", "/")
        if norm_scope == "full" or s.status in ("MODIFIED_CODE", "RISK") or not changed_files:
            active_splits.append(s)
        elif fp in changed_files:
            ranges = file_ranges.get(fp)
            if not ranges or any(
                s.start_line <= r_end and s.end_line >= r_start for r_start, r_end in ranges
            ):
                active_splits.append(s)

    new_splits = sum(1 for s in active_splits if s.status == "NEW_UNAPPROVED")
    mod_splits = sum(1 for s in active_splits if s.status in ("MODIFIED_CODE", "RISK"))

    # 3. Caller Guard (Modified symbols in working tree / staged diff)
    caller_reports = (
        CallerGuardService.get_staged_caller_guard(db, project_root, diff_text=diff_text)
        if diff_text
        else []
    )
    with_callers = [r for r in caller_reports if r.total_callers > 0]

    # 4. Hybrid Test Coverage Quality Gate (AST Call-Graph + coverage.xml / lcov.info)
    cov_gate = CoverageService.evaluate_coverage_gate(
        db=db,
        project_root=project_root,
        scope=norm_scope,
        changed_files=changed_files,
        file_ranges=file_ranges,
        min_coverage=min_coverage,
        enforce=enforce_coverage,
    )

    # 5. Build actionable agent checklist
    actions: list[str] = []
    for f in active_sec[:10]:
        actions.append(
            f"[SECURITY:{f.severity.value}] {f.cwe_id} at `{f.file_path}:L{f.start_line}` "
            f"(ID: `{f.id}`) -> Remediate code or call "
            "`vibetrace_approve_security_exception("
            f"finding_id='{f.id}', tag='...', rationale='...')`."
        )
    for f in active_qual[:5]:
        actions.append(
            f"[QUALITY:{f.severity.value}] {f.rule_id} at `{f.file_path}:L{f.start_line}` "
            f"(ID: `{f.id}`): {f.title} -> Refactor or approve into .vibetrace/quality.toml."
        )
    for s in active_splits[:10]:
        actions.append(
            f"[SPLIT:{s.status}] `{s.symbol}` at `{s.file_path}:L{s.start_line}` "
            f"({s.branches_count} branches, hash `{s.code_hash[:8]}`) -> Verify branches and call "
            f"`vibetrace_approve_split(symbol='{s.symbol}', status='approved', rationale='...')`."
        )
    for ut in cov_gate.untested_symbols[:5]:
        tag_prefix = "COVERAGE:FAIL" if not cov_gate.passed else "COVERAGE:ADVISORY"
        actions.append(
            f"[{tag_prefix}] Untested symbol `{ut.symbol}` at `{ut.file_path}:L{ut.start_line}` "
            f"(Coverage: {cov_gate.effective_coverage_pct}% vs {cov_gate.threshold_pct}% target) "
            "-> Add unit test exercising this function."
        )
    for cg in with_callers[:5]:
        actions.append(
            f"[CALLER_GUARD] Modified `{cg.symbol}` (`{cg.file_path}:L{cg.target_line}`) has "
            f"{cg.total_callers} incoming caller(s) -> Confirm caller compatibility."
        )

    passed = (
        (unapproved_sec == 0)
        and (modified_sec == 0)
        and (unapproved_qual == 0)
        and (modified_qual == 0)
        and (new_splits == 0)
        and (mod_splits == 0)
        and cov_gate.passed
    )

    return UnifiedReviewReport(
        scope=norm_scope,
        passed=passed,
        changed_files=sorted(changed_files),
        unapproved_security_count=unapproved_sec,
        modified_security_count=modified_sec,
        total_security_count=sec_report.total_findings,
        unapproved_quality_count=unapproved_qual,
        modified_quality_count=modified_qual,
        total_quality_count=qual_report.total_findings,
        new_splits_count=new_splits,
        modified_splits_count=mod_splits,
        total_splits_count=split_report.total_splits,
        approved_splits_count=split_report.approved_count,
        modified_symbols_with_callers=len(with_callers),
        coverage_gate=cov_gate,
        security_findings=active_sec,
        all_security_findings=sec_report.findings,
        quality_findings=active_qual,
        actionable_splits=active_splits,
        all_splits=split_report.splits,
        caller_guards=with_callers,
        actions_required=actions,
    )


def format_unified_review_md(report: UnifiedReviewReport) -> str:
    """Formats UnifiedReviewReport as a token-compact markdown gate for AI agents."""
    verdict = "✅ PASS (Ready to Commit)" if report.passed else "⚠️ ACTION REQUIRED"
    cg = report.coverage_gate
    if cg.effective_coverage_pct >= cg.threshold_pct:
        cov_icon = "✅"
    elif cg.enforced:
        cov_icon = "❌"
    else:
        cov_icon = "⚠️"
    lines = [
        f"## 🔍 VibeTrace Unified AI Code Review (`scope={report.scope}`): {verdict}",
        "",
        f"- **Changed Files Inspected:** {len(report.changed_files)}",
        (
            f"- **🛡️ Security, IaC & Git Secrets:** {report.unapproved_security_count} unapproved, "
            f"{report.modified_security_count} modified"
        ),
        (
            f"- **✨ Code Quality & Complexity:** {report.unapproved_quality_count} unapproved, "
            f"{report.modified_quality_count} modified"
        ),
        (
            f"- **🔀 Architectural Splits:** {report.new_splits_count} new unapproved, "
            f"{report.modified_splits_count} modified tripwires"
        ),
        (
            f"- **🧪 Quality Gate Test Coverage ({cg.coverage_source}):** {cov_icon} "
            f"{cg.effective_coverage_pct}% ({cg.tested_symbols_count}/{cg.total_target_symbols} "
            f"symbols tested — target: {cg.threshold_pct}%)"
        ),
        (
            f"- **📞 Caller Guard Advisories:** {report.modified_symbols_with_callers} "
            "modified symbol(s) with active callers"
        ),
        "",
    ]

    if not report.actions_required:
        lines.append("All security rules, split tripwires, and caller contracts are verified.")
        return "\n".join(lines)

    lines.append("### Actionable Checklist")
    for idx, act in enumerate(report.actions_required, 1):
        lines.append(f"{idx}. {act}")
    return "\n".join(lines)
