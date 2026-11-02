"""Production SQLFluff Adapter for SQL Quality, Style & Dialect Analysis.

Executes sqlfluff in subprocess with native JSON issue formatting, maps SQLFluff
rules (structure, layout, ambiguity, capitalisation) to standardized QualityFindings,
and emits automated fixes via 'sqlfluff fix --force'.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import shutil
import subprocess
from pathlib import Path
from typing import Any

from vibetrace.quality.adapters.base import LinterSubEngine, extract_json_payload
from vibetrace.quality.models import QualityCategory, QualityFinding, QualitySeverity
from vibetrace.quality.rules import get_rule_spec, resolve_canonical_rule_id

logger = logging.getLogger(__name__)

SUPPORTED_EXTENSIONS = frozenset({".sql"})

SQL_TO_SONAR: dict[str, tuple[str, str, QualityCategory, QualitySeverity]] = {
    "ST01": ("S101", "RSPEC-101", QualityCategory.RELIABILITY, QualitySeverity.HIGH),
    "ST06": ("S106", "RSPEC-106", QualityCategory.MAINTAINABILITY, QualitySeverity.MEDIUM),
    "AM04": ("S3776", "RSPEC-3776", QualityCategory.COMPLEXITY, QualitySeverity.MEDIUM),
    "CP01": ("S104", "RSPEC-104", QualityCategory.CODE_SMELL, QualitySeverity.LOW),
    "CP02": ("S104", "RSPEC-104", QualityCategory.CODE_SMELL, QualitySeverity.LOW),
    "LT01": ("S1135", "RSPEC-1135", QualityCategory.CODE_SMELL, QualitySeverity.LOW),
    "PRS": ("S2260", "RSPEC-2260", QualityCategory.RELIABILITY, QualitySeverity.HIGH),
}


class SqlFluffAdapter(LinterSubEngine):
    """Sub-engine adapter executing sqlfluff for SQL and dbt quality audits."""

    name = "sqlfluff"
    SUPPORTED_EXTENSIONS = SUPPORTED_EXTENSIONS

    def __init__(self, timeout_sec: float = 30.0) -> None:
        self.timeout_sec = timeout_sec
        self._cached_runner: list[str] | None = None

    def _find_runner(self, project_root: Path) -> list[str] | None:
        """Finds sqlfluff executable in PATH or virtual environment."""
        if self._cached_runner is not None:
            return self._cached_runner

        is_win = os.name == "nt"
        binary_name = "sqlfluff.exe" if is_win else "sqlfluff"

        candidates: list[list[str]] = []

        # 1. Active virtualenv (.venv/Scripts/sqlfluff.exe or .venv/bin/sqlfluff)
        venv_bin = project_root / ".venv" / ("Scripts" if is_win else "bin") / binary_name
        if venv_bin.is_file():
            candidates.append([str(venv_bin)])

        # 2. System PATH
        which_sql = shutil.which(binary_name)
        if which_sql:
            candidates.append([which_sql])

        # 3. ~/.vibetrace/bin
        local_bin = Path.home() / ".vibetrace" / "bin" / binary_name
        if local_bin.is_file():
            candidates.append([str(local_bin)])

        for cmd in candidates:
            try:
                res = subprocess.run(
                    [*cmd, "--version"],
                    capture_output=True,
                    text=True,
                    timeout=5.0,
                    cwd=str(project_root),
                    check=False,
                )
                if res.returncode == 0:
                    self._cached_runner = cmd
                    return cmd
            except Exception:
                continue

        return None

    def _find_dialect(self, project_root: Path) -> str:
        """Determines if an explicit dialect flag is required."""
        for cfg in (".sqlfluff", "setup.cfg"):
            if (project_root / cfg).is_file():
                return ""
        return "ansi"

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

        cmd = [*runner, "lint", "--format", "json"]
        dialect = self._find_dialect(project_root)
        if dialect:
            cmd.extend(["--dialect", dialect])

        if file_list is not None:
            rel_files: list[str] = []
            for p in file_list:
                try:
                    rel_files.append(str(p.relative_to(project_root)))
                except ValueError:
                    rel_files.append(str(p))
            if not rel_files:
                return []
            cmd.extend(rel_files)
        else:
            cmd.append(".")

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
            return self.parse_sqlfluff_output(raw_stdout, project_root)
        except Exception as e:
            logger.warning("Failed executing SQLFluff on %s: %s", project_root, e)
            return []

    def parse_sqlfluff_output(self, raw_json: str, project_root: Path) -> list[QualityFinding]:
        try:
            entries = json.loads(extract_json_payload(raw_json))
            if not isinstance(entries, list):
                return []
        except Exception:
            return []

        findings: list[QualityFinding] = []

        for entry in entries:
            file_path = str(entry.get("filepath", "")).replace("\\", "/")
            violations = entry.get("violations", [])

            for v in violations:
                code = str(v.get("code", "")).upper()
                desc = str(v.get("description", ""))
                line = int(v.get("line_no") or v.get("start_line_no") or 1)

                category, severity, rule_id, rspec_id = self._map_code(code)
                rule_spec = get_rule_spec(rule_id)

                code_hash = hashlib.sha256(f"{file_path}:{line}:{code}".encode()).hexdigest()
                finding_id = f"QUAL-SQL-{code}-{code_hash[:12]}"

                findings.append(
                    QualityFinding(
                        id=finding_id,
                        rule_id=rule_id,
                        rspec_id=rspec_id or f"RSPEC-SQL-{code}",
                        category=category,
                        severity=severity,
                        title=rule_spec.title if rule_spec else f"SQLFluff {code}: {desc[:80]}",
                        message=desc,
                        file_path=file_path,
                        start_line=line,
                        end_line=line,
                        symbol_name=None,
                        code_hash=code_hash,
                        non_compliant_snippet=None,
                        compliant_snippet=None,
                        remediation_guidance=(
                            rule_spec.explanation
                            if (rule_spec and rule_spec.explanation)
                            else f"Refactor SQL statement to comply with rule {code}."
                        ),
                    )
                )

        return findings

    def _map_code(self, code: str) -> tuple[QualityCategory, QualitySeverity, str, str | None]:
        if code in SQL_TO_SONAR:
            s_id, rspec, cat, sev = SQL_TO_SONAR[code]
            return cat, sev, f"sql:{s_id}", rspec

        category = QualityCategory.CODE_SMELL
        severity = QualitySeverity.LOW

        if code.startswith("PRS") or code.startswith("ST0"):
            severity = QualitySeverity.HIGH
            category = QualityCategory.RELIABILITY
        elif code.startswith("AM") or code.startswith("CV"):
            severity = QualitySeverity.MEDIUM
            category = QualityCategory.MAINTAINABILITY

        rule_id = resolve_canonical_rule_id(f"sql:{code}")
        return category, severity, rule_id, None

    def fix_files(
        self,
        project_root: Path,
        file_list: list[Path] | None = None,
    ) -> dict[str, Any]:
        """Runs sqlfluff fix --force to apply automated safe auto-fixes."""
        runner = self._find_runner(project_root)
        if not runner:
            return {"status": "unavailable", "fixed_files": 0}

        cmd = [*runner, "fix", "--force"]
        dialect = self._find_dialect(project_root)
        if dialect:
            cmd.extend(["--dialect", dialect])
        if file_list:
            cmd.extend([str(p.relative_to(project_root)) for p in file_list])

        try:
            res = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=self.timeout_sec,
                cwd=str(project_root),
                check=False,
            )
            return {"status": "success", "returncode": res.returncode}
        except Exception as e:
            return {"status": "error", "error": str(e)}
