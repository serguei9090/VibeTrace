"""Production Ruby Linter Adapter for Quality & Cognitive Complexity Analysis.

Executes RuboCop in subprocess with native JSON issue formatting, maps cop rules
(Metrics/AbcSize, Metrics/CyclomaticComplexity, Lint, Style) to canonical Sonar RSPEC IDs,
and emits standardized QualityFindings with automated '--autocorrect' fix support.
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

from vibetrace.quality.adapters.base import LinterSubEngine
from vibetrace.quality.models import QualityCategory, QualityFinding, QualitySeverity
from vibetrace.quality.rules import get_rule_spec, resolve_canonical_rule_id

logger = logging.getLogger(__name__)

SUPPORTED_EXTENSIONS = frozenset({".rb"})

RUBOCOP_TO_SONAR: dict[str, tuple[str, str, QualityCategory, QualitySeverity]] = {
    "Metrics/AbcSize": ("S3776", "RSPEC-3776", QualityCategory.COMPLEXITY, QualitySeverity.HIGH),
    "Metrics/CyclomaticComplexity": (
        "S1541",
        "RSPEC-1541",
        QualityCategory.COMPLEXITY,
        QualitySeverity.MEDIUM,
    ),
    "Metrics/PerceivedComplexity": (
        "S3776",
        "RSPEC-3776",
        QualityCategory.COMPLEXITY,
        QualitySeverity.HIGH,
    ),
    "Metrics/ParameterLists": (
        "S107",
        "RSPEC-107",
        QualityCategory.MAINTAINABILITY,
        QualitySeverity.MEDIUM,
    ),
    "Metrics/MethodLength": (
        "S104",
        "RSPEC-104",
        QualityCategory.MAINTAINABILITY,
        QualitySeverity.LOW,
    ),
    "Metrics/ClassLength": (
        "S104",
        "RSPEC-104",
        QualityCategory.MAINTAINABILITY,
        QualitySeverity.LOW,
    ),
    "Lint/UselessAssignment": (
        "S1854",
        "RSPEC-1854",
        QualityCategory.MAINTAINABILITY,
        QualitySeverity.LOW,
    ),
    "Lint/UnreachableCode": (
        "S1763",
        "RSPEC-1763",
        QualityCategory.RELIABILITY,
        QualitySeverity.HIGH,
    ),
    "Lint/DuplicateBranch": (
        "S1871",
        "RSPEC-1871",
        QualityCategory.CODE_SMELL,
        QualitySeverity.MEDIUM,
    ),
    "Lint/UnusedMethodArgument": (
        "S1172",
        "RSPEC-1172",
        QualityCategory.MAINTAINABILITY,
        QualitySeverity.LOW,
    ),
    "Style/NestedTernaryOperator": (
        "S3358",
        "RSPEC-3358",
        QualityCategory.MAINTAINABILITY,
        QualitySeverity.MEDIUM,
    ),
    "Style/RedundantReturn": (
        "S3626",
        "RSPEC-3626",
        QualityCategory.MAINTAINABILITY,
        QualitySeverity.LOW,
    ),
}


class RuboCopAdapter(LinterSubEngine):
    """Sub-engine adapter executing RuboCop and normalizing findings to Sonar RSPEC."""

    name = "rubocop"
    SUPPORTED_EXTENSIONS = SUPPORTED_EXTENSIONS

    def __init__(self, timeout_sec: float = 60.0) -> None:
        self.timeout_sec = timeout_sec
        self._cached_runner: list[str] | None = None

    def _find_gemfile(self, project_root: Path) -> Path | None:
        """Locates Gemfile at project root."""
        gf = project_root / "Gemfile"
        return gf if gf.is_file() else None

    def _find_runner(self, project_root: Path) -> list[str] | None:
        """Finds rubocop or bundle exec rubocop executable."""
        if self._cached_runner is not None:
            return self._cached_runner

        candidates: list[list[str]] = []
        is_win = os.name == "nt"

        # 1. Check bundler
        bundle_bin = shutil.which("bundle.bat" if is_win else "bundle")
        if bundle_bin and self._find_gemfile(project_root):
            candidates.append([bundle_bin, "exec", "rubocop"])

        # 2. Check global rubocop
        rubo_bin = shutil.which("rubocop.bat" if is_win else "rubocop")
        if rubo_bin:
            candidates.append([rubo_bin])

        # 3. Check ~/.vibetrace/bin or C:/tools/ruby*/bin
        local_rubo = Path.home() / ".vibetrace" / "bin" / ("rubocop.bat" if is_win else "rubocop")
        if local_rubo.is_file():
            candidates.append([str(local_rubo)])
        if is_win:
            for ruby_dir in Path("C:/tools").glob("ruby*/bin/rubocop.bat"):
                if ruby_dir.is_file():
                    candidates.append([str(ruby_dir)])

        for cmd in candidates:
            try:
                res = subprocess.run(
                    [*cmd, "--version"],
                    capture_output=True,
                    text=True,
                    timeout=20.0,
                    cwd=str(project_root),
                    check=False,
                )
                if res.returncode == 0:
                    self._cached_runner = cmd
                    return cmd
            except Exception:
                continue

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

        cmd = [*runner, "--format", "json"]

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
            return self.parse_rubocop_output(raw_stdout, project_root)
        except Exception as e:
            logger.warning("Failed executing RuboCop on %s: %s", project_root, e)
            return []

    def parse_rubocop_output(self, raw_json: str, project_root: Path) -> list[QualityFinding]:
        try:
            json_start = raw_json.find("{")
            json_end = raw_json.rfind("}")
            if json_start != -1 and json_end != -1:
                raw_json = raw_json[json_start : json_end + 1]
            payload = json.loads(raw_json)
        except Exception:
            return []

        findings: list[QualityFinding] = []
        files = payload.get("files", [])

        for file_entry in files:
            file_path = file_entry.get("path", "").replace("\\", "/")
            offenses = file_entry.get("offenses", [])

            for off in offenses:
                cop_name = off.get("cop_name", "")
                message = off.get("message", "")
                loc = off.get("location", {})
                line = int(loc.get("line") or loc.get("start_line") or 1)
                last_line = int(loc.get("last_line") or line)

                category, severity, rule_id, rspec_id = self._map_cop(
                    cop_name, off.get("severity", "")
                )
                rule_spec = get_rule_spec(rule_id)

                code_hash = hashlib.sha256(f"{file_path}:{line}:{cop_name}".encode()).hexdigest()
                short_id = rule_id.split(":")[-1]
                finding_id = f"QUAL-{short_id}-{code_hash[:12]}"

                findings.append(
                    QualityFinding(
                        id=finding_id,
                        rule_id=rule_id,
                        rspec_id=rspec_id or f"RSPEC-{short_id}",
                        category=category,
                        severity=severity,
                        title=rule_spec.title if rule_spec else f"{cop_name}: {message[:80]}",
                        message=message,
                        file_path=file_path,
                        start_line=line,
                        end_line=last_line,
                        symbol_name=None,
                        code_hash=code_hash,
                        non_compliant_snippet=None,
                        compliant_snippet=None,
                        remediation_guidance=(
                            rule_spec.explanation
                            if (rule_spec and rule_spec.explanation)
                            else f"Refactor code to satisfy RuboCop rule '{cop_name}'."
                        ),
                    )
                )

        return findings

    def _map_cop(
        self, cop_name: str, rubo_severity: str
    ) -> tuple[QualityCategory, QualitySeverity, str, str | None]:
        if cop_name in RUBOCOP_TO_SONAR:
            s_id, rspec, cat, sev = RUBOCOP_TO_SONAR[cop_name]
            return cat, sev, f"ruby:{s_id}", rspec

        sev_lower = rubo_severity.lower()
        if sev_lower in ("fatal", "error"):
            severity = QualitySeverity.HIGH
            category = QualityCategory.RELIABILITY
        elif sev_lower == "warning":
            severity = QualitySeverity.MEDIUM
            category = QualityCategory.MAINTAINABILITY
        elif sev_lower in ("refactor", "convention"):
            severity = QualitySeverity.LOW
            category = QualityCategory.CODE_SMELL
        else:
            severity = QualitySeverity.LOW
            category = QualityCategory.CODE_SMELL

        safe_cop = cop_name.replace("/", "-")
        rule_id = resolve_canonical_rule_id(f"ruby:{safe_cop}")
        return category, severity, rule_id, None

    def fix_files(
        self,
        project_root: Path,
        file_list: list[Path] | None = None,
    ) -> dict[str, Any]:
        """Runs rubocop -a to apply automated safe auto-fixes."""
        runner = self._find_runner(project_root)
        if not runner:
            return {"status": "unavailable", "fixed_files": 0}

        cmd = [*runner, "-a", "--format", "json"]
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
