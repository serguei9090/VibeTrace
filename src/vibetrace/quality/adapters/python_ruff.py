"""Production Ruff Adapter for Python Quality & Clean Code Analysis.

Executes Ruff with isolated VibeTrace Sonar-grade presets, maps raw Ruff codes
(B006, SIM102, E722) to canonical Sonar RSPEC IDs, and emits structured QualityFindings.
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
from vibetrace.quality.rules import QUALITY_RULES_CATALOG, get_rule_spec, resolve_canonical_rule_id

logger = logging.getLogger(__name__)

BUNDLED_PRESET_PATH = (
    Path(__file__).parent.parent.parent / "resources" / "presets" / "ruff_sonar.toml"
)


class RuffAdapter(LinterSubEngine):
    """Sub-engine adapter executing Ruff in subprocess and normalizing findings to Sonar RSPEC."""

    name = "ruff"

    def __init__(self, timeout_sec: float = 15.0) -> None:
        self.timeout_sec = timeout_sec
        self._cached_runner: list[str] | None = None

    def _resolve_config_path(self, project_root: Path) -> Path:
        """Prefers local .vibetrace/config/ruff.toml if present, falling back to bundled preset."""
        override = project_root / ".vibetrace" / "config" / "ruff.toml"
        if override.is_file():
            return override
        return BUNDLED_PRESET_PATH

    def _find_runner(self, project_root: Path) -> list[str] | None:
        """Determines the best executable runner for Ruff in the workspace."""
        if self._cached_runner is not None:
            return self._cached_runner

        # 1. Check local virtual environment (.venv)
        is_win = os.name == "nt"
        venv_bin = "Scripts" if is_win else "bin"
        exe_suffix = ".exe" if is_win else ""
        venv_ruff = project_root / ".venv" / venv_bin / f"ruff{exe_suffix}"
        if venv_ruff.is_file():
            self._cached_runner = [str(venv_ruff)]
            return self._cached_runner

        # 2. Check uv in project
        if shutil.which("uv"):
            self._cached_runner = ["uv", "run", "ruff"]
            return self._cached_runner

        # 3. Check global ruff
        if shutil.which("ruff"):
            self._cached_runner = ["ruff"]
            return self._cached_runner

        return None

    def is_available(self, project_root: Path) -> bool:
        """Verifies if Ruff can be executed."""
        runner = self._find_runner(project_root)
        if not runner:
            return False
        try:
            res = subprocess.run(
                [*runner, "--version"],
                capture_output=True,
                text=True,
                check=False,
                timeout=5.0,
            )
            return res.returncode == 0
        except (OSError, subprocess.SubprocessError):
            return False

    def _run_ruff(self, args: list[str], project_root: Path) -> list[dict[str, Any]]:
        """Executes Ruff with JSON output formatting and returns parsed diagnostics."""
        runner = self._find_runner(project_root)
        if not runner:
            return []

        config_path = self._resolve_config_path(project_root)
        cmd = [*runner, "check", "--config", str(config_path), "--output-format=json", *args]

        try:
            res = subprocess.run(
                cmd,
                cwd=str(project_root),
                capture_output=True,
                text=True,
                check=False,
                timeout=self.timeout_sec,
            )
            if not res.stdout.strip():
                return []
            parsed = json.loads(res.stdout)
            return parsed if isinstance(parsed, list) else []
        except (OSError, subprocess.SubprocessError, json.JSONDecodeError) as err:
            logger.debug("Ruff execution encountered: %s", err)
            return []

    def _map_item_to_finding(
        self,
        item: dict[str, Any],
        project_root: Path,
    ) -> QualityFinding | None:
        """Translates a raw Ruff JSON diagnostic item into a Sonar-mapped QualityFinding."""
        code = str(item.get("code") or "").strip()
        file_path_str = str(item.get("filename") or "").replace("\\", "/")
        try:
            rel_path = Path(file_path_str).relative_to(project_root).as_posix()
        except ValueError:
            rel_path = file_path_str

        loc = item.get("location") or {}
        end_loc = item.get("end_location") or loc
        start_line = int(loc.get("row") or 1)
        end_line = int(end_loc.get("row") or start_line)
        message = str(item.get("message") or "")

        # Map Ruff vendor code to canonical Sonar RSPEC rule
        canonical_id = resolve_canonical_rule_id(code)
        spec = get_rule_spec(canonical_id) or QUALITY_RULES_CATALOG.get(code)

        if spec:
            rule_id = f"python:{spec.rule_id}"
            rspec_id = spec.rspec_id
            category = spec.category
            severity = spec.severity
            title = spec.title
            compliant_snippet = spec.compliant_example
            remediation_guidance = spec.explanation
            cwe_ids = list(spec.cwe_ids) if spec.cwe_ids else []
            owasp = spec.owasp
        else:
            rule_id = f"python:{code}"
            rspec_id = f"RUFF-{code}"
            category = QualityCategory.CODE_SMELL
            if code.startswith(("B", "E", "F")):
                severity = QualitySeverity.MEDIUM
            else:
                severity = QualitySeverity.LOW
            title = message
            compliant_snippet = None
            remediation_guidance = (
                f"Consult Ruff rule documentation: https://docs.astral.sh/ruff/rules/{code}"
            )
            cwe_ids = []
            owasp = None

        raw_id = f"{rel_path}:{start_line}:{code}:{message}"
        fid = f"QUAL-{code}-{hashlib.sha256(raw_id.encode('utf-8')).hexdigest()[:12]}"
        code_hash = hashlib.sha256(message.encode("utf-8")).hexdigest()

        return QualityFinding(
            id=fid,
            rule_id=rule_id,
            rspec_id=rspec_id,
            category=category,
            severity=severity,
            title=title,
            message=message,
            file_path=rel_path,
            start_line=start_line,
            end_line=end_line,
            symbol_name=None,
            code_hash=code_hash,
            non_compliant_snippet=None,
            compliant_snippet=compliant_snippet,
            remediation_guidance=remediation_guidance,
            cwe_ids=cwe_ids,
            owasp_category=owasp,
        )

    def audit_file(self, file_path: Path, project_root: Path) -> list[QualityFinding]:
        """Audits a single target Python file."""
        if file_path.suffix.lower() != ".py":
            return []
        items = self._run_ruff([str(file_path)], project_root)
        findings: list[QualityFinding] = []
        for item in items:
            f = self._map_item_to_finding(item, project_root)
            if f:
                findings.append(f)
        return findings

    def audit_project(
        self,
        project_root: Path,
        file_list: list[Path] | None = None,
    ) -> list[QualityFinding]:
        """Audits target files or entire project root in batch mode."""
        args: list[str] = []
        if file_list is not None:
            py_files = [str(p) for p in file_list if p.suffix.lower() == ".py"]
            if not py_files:
                return []
            args = py_files
        else:
            args = ["."]

        items = self._run_ruff(args, project_root)
        findings: list[QualityFinding] = []
        for item in items:
            f = self._map_item_to_finding(item, project_root)
            if f:
                findings.append(f)
        return findings
