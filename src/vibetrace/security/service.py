"""Orchestrator service for scanning project files and evaluating security baselines."""

from __future__ import annotations

from pathlib import Path

from vibetrace.indexer.db import IndexerDB
from vibetrace.security.ast_scanner import AstSecurityScanner
from vibetrace.security.baseline import SecurityBaselineManager
from vibetrace.security.models import SecurityAuditReport, SecurityFinding


class SecurityService:
    """Provides high-level security audit and exception recording workflows."""

    @classmethod
    def audit_project(
        cls, db: IndexerDB, project_root: Path, file_filter: str | None = None
    ) -> SecurityAuditReport:
        """Runs AST security scanner across indexed files and applies the Git baseline."""
        findings: list[SecurityFinding] = []

        if file_filter:
            target_files = [file_filter]
        else:
            # Query distinct indexed files from database
            with db._get_conn() as conn:
                cursor = conn.execute(
                    "SELECT DISTINCT file_path FROM nodes WHERE file_path IS NOT NULL"
                )
                rows = cursor.fetchall()
                target_files = [r[0] for r in rows if r[0]]

        for f_path in target_files:
            file_findings = AstSecurityScanner.scan_file(f_path, project_root)
            findings.extend(file_findings)

        return SecurityBaselineManager.apply_baseline_to_findings(findings, project_root)

    @classmethod
    def record_exception(
        cls,
        project_root: Path,
        finding_id: str,
        rule_id: str,
        file_path: str,
        code_hash: str,
        rationale: str,
        severity: str = "HIGH",
        status: str = "APPROVED",
        reviewed_by: str = "developer",
    ) -> None:
        """Records an approved exception in .vibetrace/security.toml."""
        SecurityBaselineManager.record_exception(
            project_root=project_root,
            finding_id=finding_id,
            rule_id=rule_id,
            file_path=file_path,
            code_hash=code_hash,
            rationale=rationale,
            severity=severity,
            status=status,
            reviewed_by=reviewed_by,
        )
