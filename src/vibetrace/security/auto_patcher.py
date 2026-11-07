"""Automated Dependency Auto-Patcher and Reachability Triage Service.

Resolves minimal non-vulnerable versions for package CVEs and generates native
package manager commands (uv, bun, cargo, go). For unpatched or zero-day CVEs,
verifies whether the package is actually reachable in application code via AST
analysis and proposes call-site guards or baseline suppressions.
"""

from __future__ import annotations

import ast
import re
import shlex
import subprocess
import sys
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from vibetrace.indexer.db import IndexerDB
from vibetrace.security.baseline import SecurityBaselineManager
from vibetrace.security.models import FindingCategory, SecurityFinding


@dataclass
class DependencyPatchAction:
    """Action plan for a single vulnerable dependency."""

    package_name: str
    current_version: str
    fixed_version: str | None
    ecosystem: str
    lockfile_path: str
    cve_ids: list[str]
    severity: str
    action_type: str  # UPGRADE | UNPATCHED_UNREACHABLE | UNPATCHED_REACHABLE
    reachability_callers: int
    referring_files: list[str]
    proposed_command: str | None
    mitigation_guidance: str
    status: str = "PENDING"  # PENDING | APPLIED | SUPPRESSED | FAILED

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class AutoPatchReport:
    """Aggregated dependency auto-patching and remediation plan."""

    total_vulnerable_packages: int = 0
    upgradable_count: int = 0
    unpatched_unreachable_count: int = 0
    unpatched_reachable_count: int = 0
    applied_count: int = 0
    suppressed_count: int = 0
    actions: list[DependencyPatchAction] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def check_package_reachability(
    project_root: Path,
    package_name: str,
    db: IndexerDB | None = None,
) -> tuple[int, list[str]]:
    """Evaluates whether a package is imported or referenced in application source code.

    Returns:
        (caller_count, list_of_referring_files)
    """
    normalized_name = package_name.lower().replace("-", "_")
    referring_files: set[str] = set()
    caller_count = 0

    # 1. First search SQLite index if available
    if db is not None:
        try:
            with db._get_conn() as conn:
                rows = conn.execute(
                    """
                    SELECT DISTINCT file_path FROM nodes
                    WHERE file_path NOT LIKE 'tests/%'
                      AND file_path NOT LIKE 'scratch/%'
                      AND file_path NOT LIKE 'docs/%'
                      AND (
                          lower(name) = ?
                          OR lower(qualified_name) LIKE ?
                      )
                    """,
                    (normalized_name, f"{normalized_name}.%"),
                ).fetchall()
                for row in rows:
                    referring_files.add(row["file_path"])
        except Exception:
            pass

    # 2. Check AST imports across source files in workspace
    py_files = [
        p
        for p in project_root.rglob("*.py")
        if not any(
            part in p.parts
            for part in (
                ".venv",
                "node_modules",
                "tests",
                "scratch",
                ".pytest_cache",
                ".ruff_cache",
            )
        )
    ]

    import_pattern = re.compile(rf"^\s*(?:import\s+{normalized_name}\b|from\s+{normalized_name}\b)")

    for py_file in py_files:
        try:
            text = py_file.read_text(encoding="utf-8", errors="ignore")
            if normalized_name not in text:
                continue

            # Quick regex pre-filter before AST parse
            if not any(import_pattern.search(line) for line in text.splitlines()):
                continue

            tree = ast.parse(text, filename=str(py_file))
            found_import = False
            for node in ast.walk(tree):
                if isinstance(node, ast.Import):
                    for alias in node.names:
                        if alias.name.split(".")[0].lower() == normalized_name:
                            found_import = True
                            caller_count += 1
                elif isinstance(node, ast.ImportFrom):
                    if node.module and node.module.split(".")[0].lower() == normalized_name:
                        found_import = True
                        caller_count += 1

            if found_import:
                rel_path = str(py_file.relative_to(project_root)).replace("\\", "/")
                referring_files.add(rel_path)
        except Exception:
            continue

    # Also check TS/JS files if in npm ecosystem
    ts_files = [
        p
        for p in project_root.rglob("*.ts")
        if not any(
            part in p.parts for part in (".venv", "node_modules", "dist", "build", "e2e", "tests")
        )
    ]
    for ts_file in ts_files:
        try:
            text = ts_file.read_text(encoding="utf-8", errors="ignore")
            if f'"{package_name}"' in text or f"'{package_name}'" in text:
                rel_path = str(ts_file.relative_to(project_root)).replace("\\", "/")
                referring_files.add(rel_path)
                caller_count += 1
        except Exception:
            continue

    return max(caller_count, len(referring_files)), sorted(referring_files)


class DependencyAutoPatcher:
    """Orchestrates dependency remediation plans, native PM executions, and reachability triage."""

    @classmethod
    def build_patch_plan(
        cls,
        project_root: Path,
        findings: list[SecurityFinding],
        db: IndexerDB | None = None,
        package_filter: str | None = None,
    ) -> AutoPatchReport:
        """Analyzes package CVE findings and constructs structured remediation actions."""
        # Filter for package CVEs
        cve_findings = [f for f in findings if f.category == FindingCategory.PACKAGE_CVE]

        # Group findings by (ecosystem, package_name)
        grouped: dict[tuple[str, str], list[SecurityFinding]] = {}
        for f in cve_findings:
            pkg_name = f.symbol_name or f.metadata.get("package_name") or ""
            if not pkg_name:
                continue
            if package_filter and pkg_name.lower() != package_filter.lower():
                continue
            eco = f.metadata.get("ecosystem", "PyPI")
            grouped.setdefault((eco, pkg_name), []).append(f)

        report = AutoPatchReport(total_vulnerable_packages=len(grouped))

        for (eco, pkg_name), pkg_findings in grouped.items():
            if not pkg_findings:
                continue
            first = pkg_findings[0]
            current_ver = ""
            if "@" in first.title:
                try:
                    current_ver = first.title.split("@")[1].split(" ")[0]
                except Exception:
                    pass

            cve_ids = [str(f.metadata.get("cve_id", f.id)) for f in pkg_findings]
            max_severity = "LOW"
            sev_order = ["INFO", "LOW", "MEDIUM", "HIGH", "CRITICAL"]
            for f in pkg_findings:
                if sev_order.index(f.severity.value) > sev_order.index(max_severity):
                    max_severity = f.severity.value

            # Extract highest fixed_version across findings
            fixed_version: str | None = None
            for f in pkg_findings:
                fv = f.metadata.get("fixed_version")
                if fv and fv not in ("latest", "UNKNOWN", "none", ""):
                    fixed_version = fv

            # Check reachability in codebase
            callers, ref_files = check_package_reachability(project_root, pkg_name, db=db)

            # Determine action type and native commands
            if fixed_version:
                action_type = "UPGRADE"
                report.upgradable_count += 1
                cmd = cls._build_upgrade_command(eco, pkg_name, fixed_version)
                guidance = (
                    f"Upgrade `{pkg_name}` to safe release `{fixed_version}` "
                    f"via native package manager."
                )
            else:
                cmd = None
                if callers == 0:
                    action_type = "UNPATCHED_UNREACHABLE"
                    report.unpatched_unreachable_count += 1
                    guidance = (
                        f"Unpatched upstream CVE, but `{pkg_name}` has 0 callers in "
                        f"application code. Safe to prune or suppress as compensating control."
                    )
                else:
                    action_type = "UNPATCHED_REACHABLE"
                    report.unpatched_reachable_count += 1
                    guidance = (
                        f"Unpatched upstream CVE and `{pkg_name}` is actively called by "
                        f"{callers} symbol(s) in {len(ref_files)} file(s). "
                        f"Apply call-site input validation guards or manifest git fork."
                    )

            action = DependencyPatchAction(
                package_name=pkg_name,
                current_version=current_ver,
                fixed_version=fixed_version,
                ecosystem=eco,
                lockfile_path=first.file_path,
                cve_ids=cve_ids,
                severity=max_severity,
                action_type=action_type,
                reachability_callers=callers,
                referring_files=ref_files,
                proposed_command=cmd,
                mitigation_guidance=guidance,
                status="PENDING",
            )
            report.actions.append(action)

        # Sort: UPGRADE first, then UNPATCHED_REACHABLE, then UNPATCHED_UNREACHABLE
        type_prio = {"UPGRADE": 0, "UNPATCHED_REACHABLE": 1, "UNPATCHED_UNREACHABLE": 2}
        report.actions.sort(key=lambda a: (type_prio.get(a.action_type, 3), a.package_name))
        return report

    @classmethod
    def _build_upgrade_command(cls, ecosystem: str, package_name: str, fixed_version: str) -> str:
        """Constructs the native package manager command for the target ecosystem."""
        eco = ecosystem.lower()
        if eco in ("pypi", "python"):
            return f"uv lock --upgrade-package {package_name}"
        if eco in ("npm", "javascript", "typescript"):
            return f"bun update {package_name}"
        if eco in ("cargo", "rust", "crates.io"):
            return f"cargo update -p {package_name}"
        if eco in ("go", "golang"):
            return f"go get {package_name}@v{fixed_version}"
        return f"uv lock --upgrade-package {package_name}"

    @classmethod
    def apply_patch_plan(
        cls,
        project_root: Path,
        report: AutoPatchReport,
        suppress_unpatched: bool = False,
    ) -> AutoPatchReport:
        """Executes native package manager commands and records baseline suppressions."""
        for action in report.actions:
            if action.action_type == "UPGRADE" and action.proposed_command:
                try:
                    cmd_args = shlex.split(
                        action.proposed_command,
                        posix=(sys.platform != "win32"),
                    )
                    res = subprocess.run(
                        cmd_args,
                        shell=False,
                        cwd=str(project_root),
                        capture_output=True,
                        text=True,
                        timeout=60,
                        check=False,
                    )
                    if res.returncode == 0:
                        action.status = "APPLIED"
                        report.applied_count += 1
                    else:
                        action.status = "FAILED"
                        err_snip = res.stderr.strip()[:100]
                        action.mitigation_guidance += f" (Command failed: {err_snip})"
                except Exception as exc:
                    action.status = "FAILED"
                    action.mitigation_guidance += f" (Execution error: {exc})"

            elif (
                suppress_unpatched
                and action.action_type == "UNPATCHED_UNREACHABLE"
                and action.reachability_callers == 0
            ):
                # Automatically suppress uncalled unpatched dependencies
                cve_0 = action.cve_ids[0]
                finding_id = f"SEC-{cve_0}-{action.package_name}-{action.current_version}"
                code_hash = f"cve:{action.ecosystem}:{action.package_name}:{action.current_version}"
                SecurityBaselineManager.record_exception(
                    project_root=project_root,
                    finding_id=finding_id,
                    rule_id=f"{action.ecosystem.lower()}:{cve_0}",
                    file_path=action.lockfile_path,
                    code_hash=code_hash,
                    rationale=(
                        "Unpatched upstream CVE with 0 callers in application code; "
                        "mitigated by dead-code isolation."
                    ),
                    severity=action.severity,
                    status="APPROVED",
                    reviewed_by="vibetrace-auto-patcher",
                    category="package_cve",
                    symbol_name=action.package_name,
                    tag="compensating-control",
                )
                action.status = "SUPPRESSED"
                report.suppressed_count += 1

        return report
