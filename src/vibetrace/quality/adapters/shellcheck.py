"""Production ShellCheck Adapter for Shell & Bash Quality Analysis.

Executes shellcheck in subprocess with JSON output formatting, maps SC rules
(SC2086 unquoted variables, SC2181 check exit code, SC2155, SC2164) to standardized
QualityFindings.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import shutil
import subprocess
from pathlib import Path

from vibetrace.quality.adapters.base import LinterSubEngine
from vibetrace.quality.models import QualityCategory, QualityFinding, QualitySeverity
from vibetrace.quality.rules import get_rule_spec, resolve_canonical_rule_id

logger = logging.getLogger(__name__)

SUPPORTED_EXTENSIONS = frozenset({".sh", ".bash", ".zsh", ".ksh"})

SHELL_TO_SONAR: dict[int, tuple[str, str, QualityCategory, QualitySeverity]] = {
    2086: ("S2086", "RSPEC-2086", QualityCategory.RELIABILITY, QualitySeverity.HIGH),
    2046: ("S2046", "RSPEC-2046", QualityCategory.RELIABILITY, QualitySeverity.HIGH),
    2164: ("S2164", "RSPEC-2164", QualityCategory.RELIABILITY, QualitySeverity.HIGH),
    2155: ("S2155", "RSPEC-2155", QualityCategory.RELIABILITY, QualitySeverity.MEDIUM),
    2181: ("S2181", "RSPEC-2181", QualityCategory.CODE_SMELL, QualitySeverity.LOW),
    2006: ("S2006", "RSPEC-2006", QualityCategory.CODE_SMELL, QualitySeverity.LOW),
    2002: ("S2002", "RSPEC-2002", QualityCategory.CODE_SMELL, QualitySeverity.LOW),  # useless cat
}


class ShellCheckAdapter(LinterSubEngine):
    """Sub-engine adapter executing shellcheck on shell scripts."""

    name = "shellcheck"
    SUPPORTED_EXTENSIONS = SUPPORTED_EXTENSIONS

    def __init__(self, timeout_sec: float = 30.0) -> None:
        self.timeout_sec = timeout_sec
        self._cached_runner: list[str] | None = None

    def _find_runner(self, project_root: Path) -> list[str] | None:
        """Finds shellcheck executable in PATH."""
        if self._cached_runner is not None:
            return self._cached_runner

        is_win = os.name == "nt"
        binary = shutil.which("shellcheck.exe" if is_win else "shellcheck")
        if not binary:
            # Check ~/.vibetrace/bin
            bin_name = "shellcheck.exe" if is_win else "shellcheck"
            local_bin = Path.home() / ".vibetrace" / "bin" / bin_name
            if local_bin.is_file():
                binary = str(local_bin)

        if binary:
            try:
                res = subprocess.run(
                    [binary, "-V"],
                    capture_output=True,
                    text=True,
                    timeout=5.0,
                    check=False,
                )
                if res.returncode == 0:
                    self._cached_runner = [binary]
                    return [binary]
            except Exception:
                pass

        return None

    def is_available(self, project_root: Path) -> bool:
        return self._find_runner(project_root) is not None

    def audit_file(self, file_path: Path, project_root: Path) -> list[QualityFinding]:
        return self.audit_project(project_root, [file_path])

    def audit_project(
        self,
        project_root: Path,
        file_list: list[Path] | None = None,
    ) -> list[QualityFinding]:
        runner = self._find_runner(project_root)
        if not runner:
            return []

        cmd = [*runner, "-f", "json"]

        if file_list is not None:
            rel_files = []
            for p in file_list:
                try:
                    rel_files.append(str(p.relative_to(project_root)))
                except ValueError:
                    rel_files.append(str(p))
            if not rel_files:
                return []
            cmd.extend(rel_files)
        else:
            # Audit all shell files
            matches = [
                str(p.relative_to(project_root))
                for p in project_root.rglob("*")
                if p.suffix.lower() in SUPPORTED_EXTENSIONS and p.is_file()
            ]
            if not matches:
                return []
            cmd.extend(matches)

        try:
            res = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=self.timeout_sec,
                cwd=str(project_root),
                check=False,
            )
            raw_stdout = res.stdout.strip()
            if not raw_stdout:
                return []
            return self.parse_shellcheck_output(raw_stdout, project_root)
        except Exception as e:
            logger.warning("Failed executing ShellCheck on %s: %s", project_root, e)
            return []

    def parse_shellcheck_output(self, raw_json: str, project_root: Path) -> list[QualityFinding]:
        try:
            items = json.loads(raw_json)
            if not isinstance(items, list):
                return []
        except Exception:
            return []

        findings: list[QualityFinding] = []

        for item in items:
            file_path = str(item.get("file", "")).replace("\\", "/")
            line = int(item.get("line") or 1)
            end_line = int(item.get("endLine") or line)
            code = int(item.get("code") or 0)
            level = str(item.get("level", "warning")).lower()
            message = str(item.get("message", ""))

            category, severity, rule_id, rspec_id = self._map_sc(code, level)
            rule_spec = get_rule_spec(rule_id)

            code_hash = hashlib.sha256(f"{file_path}:{line}:SC{code}".encode()).hexdigest()
            finding_id = f"QUAL-SC{code}-{code_hash[:12]}"

            findings.append(
                QualityFinding(
                    id=finding_id,
                    rule_id=rule_id,
                    rspec_id=rspec_id or f"RSPEC-SC{code}",
                    category=category,
                    severity=severity,
                    title=rule_spec.title if rule_spec else f"ShellCheck SC{code}: {message[:80]}",
                    message=message,
                    file_path=file_path,
                    start_line=line,
                    end_line=end_line,
                    symbol_name=None,
                    code_hash=code_hash,
                    non_compliant_snippet=None,
                    compliant_snippet=None,
                    remediation_guidance=(
                        rule_spec.explanation
                        if (rule_spec and rule_spec.explanation)
                        else f"Consult ShellCheck wiki for SC{code} remediation."
                    ),
                )
            )

        return findings

    def _map_sc(
        self, code: int, level: str
    ) -> tuple[QualityCategory, QualitySeverity, str, str | None]:
        if code in SHELL_TO_SONAR:
            s_id, rspec, cat, sev = SHELL_TO_SONAR[code]
            return cat, sev, f"shell:{s_id}", rspec

        if level == "error":
            severity = QualitySeverity.HIGH
            category = QualityCategory.RELIABILITY
        elif level == "warning":
            severity = QualitySeverity.MEDIUM
            category = QualityCategory.MAINTAINABILITY
        elif level == "info":
            severity = QualitySeverity.LOW
            category = QualityCategory.CODE_SMELL
        else:
            severity = QualitySeverity.LOW
            category = QualityCategory.CODE_SMELL

        rule_id = resolve_canonical_rule_id(f"shell:SC{code}")
        return category, severity, rule_id, None
