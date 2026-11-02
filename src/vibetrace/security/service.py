"""Orchestrator service for scanning project files and evaluating security baselines."""

from __future__ import annotations

from pathlib import Path

from vibetrace.indexer.db import IndexerDB
from vibetrace.security.ast_scanner import AstSecurityScanner
from vibetrace.security.baseline import SecurityBaselineManager
from vibetrace.security.git_secret_scanner import GitSecretScanner
from vibetrace.security.iac_scanner import IacScanner
from vibetrace.security.lockfile_parser import LockfileParser
from vibetrace.security.models import SecurityAuditReport, SecurityFinding
from vibetrace.security.osv_client import OsvClient
from vibetrace.security.rule_sync import RuleCatalogManager
from vibetrace.security.taint_engine import TaintEngine
from vibetrace.taxonomy import CweTaxonomyManager


class SecurityService:
    """Provides high-level security audit and exception recording workflows."""

    @staticmethod
    def _is_test_path(rel_path: str) -> bool:
        norm = rel_path.replace("\\", "/")
        parts = norm.split("/")
        if any(p in ("tests", "test", "e2e", "__tests__") for p in parts[:-1]):
            return True
        fname = parts[-1]
        return fname.startswith("test_") or fname.endswith(
            ("_test.py", "_test.go", ".spec.ts", ".test.ts", ".spec.tsx")
        )

    @classmethod
    def audit_project(
        cls,
        db: IndexerDB,
        project_root: Path,
        file_filter: str | None = None,
        file_list: list[str] | None = None,
        check_dependencies: bool = True,
        force_update_cves: bool = False,
        analyze_taint: bool = True,
        include_tests: bool = False,
        git_commit_depth: int = 10,
    ) -> SecurityAuditReport:
        """Runs AST security scanner and lockfile CVE checks, then applies Git baseline."""
        findings: list[SecurityFinding] = []

        if file_filter:
            target_files = [file_filter]
        elif file_list is not None:
            target_files = (
                file_list if include_tests else [f for f in file_list if not cls._is_test_path(f)]
            )
        else:
            with db._get_conn() as conn:
                cursor = conn.execute(
                    "SELECT DISTINCT file_path FROM nodes WHERE file_path IS NOT NULL"
                )
                rows = cursor.fetchall()
                all_files = [r[0] for r in rows if r[0]]
            if include_tests:
                target_files = all_files
            else:
                non_tests = [f for f in all_files if not cls._is_test_path(f)]
                target_files = non_tests if non_tests else all_files

        # 1. Source code AST + declarative rule catalog inspection
        decl_rules = RuleCatalogManager.sync_and_load_rules(
            db=db, project_root=project_root, force_update=force_update_cves
        )
        for f_path in target_files:
            file_findings = AstSecurityScanner.scan_file(f_path, project_root)
            file_findings.extend(
                RuleCatalogManager.scan_file_with_rules(f_path, project_root, decl_rules)
            )
            findings.extend(file_findings)

        # 1b. Call-graph + intra-procedural Def-Use reachability from untrusted entry points
        if analyze_taint:
            findings = TaintEngine.analyze(
                db,
                findings,
                frozenset(SecurityBaselineManager.load_sanitizers(project_root)),
                project_root=project_root,
            )

        # 1c. Zero-dependency IaC scanning (Dockerfile, docker-compose, K8s, GitHub Actions)
        findings.extend(IacScanner.scan_project(project_root, file_filter=file_filter))

        # 1d. Gitleaks-style Secret & Shannon Entropy scanning (working tree + last N commits)
        if not file_filter:
            findings.extend(
                GitSecretScanner.scan_repository(
                    project_root=project_root,
                    commit_depth=git_commit_depth,
                    include_tests=include_tests,
                )
            )

        # 2. Dependency lockfile CVE scanning (Google OSV)
        if check_dependencies and not file_filter:
            packages = LockfileParser.parse_all_lockfiles(project_root)
            if packages:
                cve_findings = OsvClient.query_vulnerabilities(
                    packages=packages,
                    db=db,
                    force_update=force_update_cves,
                )
                if cve_findings:
                    try:
                        from vibetrace.security.dep_graph import (
                            DependencyGraphBuilder,
                            normalize_package_name,
                        )

                        dep_graph = DependencyGraphBuilder.build(project_root)
                        for cf in cve_findings:
                            if cf.symbol_name:
                                norm_name = normalize_package_name(cf.symbol_name)
                                if norm_name not in dep_graph.direct_roots:
                                    chains = dep_graph.resolve_why(norm_name)
                                    if chains:
                                        chain_str = " -> ".join(chains[0].path)
                                        cf.remediation_guidance = (
                                            f"{cf.remediation_guidance}\n\n"
                                            f"**Transitive Root Cause:** `{chain_str}` "
                                            f"(via direct parent `{chains[0].root_package}`). "
                                            f"Upgrade `{chains[0].root_package}` to resolve."
                                        )
                    except Exception:
                        pass
                findings.extend(cve_findings)

        for f in findings:
            CweTaxonomyManager.enrich_finding(f)

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
        category: str = "ast_pattern",
        symbol_name: str | None = None,
        tag: str | None = None,
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
            category=category,
            symbol_name=symbol_name,
            tag=tag,
        )

    @classmethod
    def build_remediation_bundle(
        cls,
        db: IndexerDB,
        project_root: Path,
        finding_id: str | None = None,
        file_filter: str | None = None,
    ) -> dict[str, object]:
        """Generates structured AI remediation instructions and diff guidance for findings."""
        report = cls.audit_project(
            db=db,
            project_root=project_root,
            file_filter=file_filter,
            check_dependencies=finding_id is not None,
        )
        targets = [
            f
            for f in report.findings
            if (finding_id and f.id == finding_id) or (not finding_id and f.status != "APPROVED")
        ]
        remediations = []
        for f in targets:
            prompt = (
                f"Remediate {f.cwe_id} ({f.severity}) '{f.title}' in "
                f"{f.file_path}:{f.start_line}-{f.end_line}.\n"
                f"Issue: {f.message}\n"
                f"Guidance: {f.remediation_guidance or ''}\n"
                f"Replace vulnerable code:\n{f.non_compliant_snippet or ''}\n"
                f"With compliant pattern:\n{f.compliant_snippet or ''}"
            )
            taint_slices = f.metadata.get("taint_slices")
            if taint_slices:
                prompt += "\n\nVerified Multi-Hop Taint Lineage:"
                for s in taint_slices:
                    prompt += (
                        f"\n[Hop {s['step']}: {s['symbol']} in "
                        f"{s['file']}:{s['start_line']}-{s['end_line']}]\n{s['snippet']}"
                    )

            remediations.append(
                {
                    "finding_id": f.id,
                    "cwe_id": f.cwe_id,
                    "severity": f.severity,
                    "file_path": f.file_path,
                    "start_line": f.start_line,
                    "end_line": f.end_line,
                    "symbol_name": f.symbol_name,
                    "non_compliant_snippet": f.non_compliant_snippet,
                    "compliant_snippet": f.compliant_snippet,
                    "taint_slices": taint_slices,
                    "ai_fix_prompt": prompt.strip(),
                }
            )
        return {"total_remediations": len(remediations), "remediations": remediations}
