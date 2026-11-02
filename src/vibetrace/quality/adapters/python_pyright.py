"""Production Pyright Adapter for Python Semantic Type & Coroutine Analysis.

Executes Pyright in subprocess with --outputjson, extracts type errors,
unawaited coroutines, and type mismatches, mapping them to Sonar QualityFinding specs.
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

logger = logging.getLogger(__name__)

BUNDLED_PRESET_PATH = (
    Path(__file__).parent.parent.parent / "resources" / "presets" / "pyright_sonar.json"
)


class PyrightAdapter(LinterSubEngine):
    """Sub-engine adapter executing Pyright in subprocess and extracting type diagnostics."""

    name = "pyright"

    def __init__(self, timeout_sec: float = 25.0) -> None:
        self.timeout_sec = timeout_sec
        self._cached_runner: list[str] | None = None

    def _resolve_config_path(self, project_root: Path) -> Path:
        """Prefers local .vibetrace/config/pyright.json if present, or bundled preset."""
        override = project_root / ".vibetrace" / "config" / "pyright.json"
        if override.is_file():
            return override
        return BUNDLED_PRESET_PATH

    def _find_runner(self, project_root: Path) -> list[str] | None:
        """Determines the best executable runner for Pyright in the workspace."""
        if self._cached_runner is not None:
            return self._cached_runner

        is_win = os.name == "nt"
        venv_bin = "Scripts" if is_win else "bin"
        cmd_suffix = ".cmd" if is_win else ""
        venv_pyright = project_root / ".venv" / venv_bin / f"pyright{cmd_suffix}"
        if venv_pyright.is_file():
            self._cached_runner = [str(venv_pyright)]
            return self._cached_runner

        if shutil.which("uv"):
            self._cached_runner = ["uv", "run", "pyright"]
            return self._cached_runner

        if shutil.which("pyright"):
            self._cached_runner = ["pyright"]
            return self._cached_runner

        return None

    def is_available(self, project_root: Path) -> bool:
        """Verifies if Pyright can be executed."""
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

    def _run_pyright(self, args: list[str], project_root: Path) -> list[dict[str, Any]]:
        """Executes Pyright with JSON output formatting and returns parsed diagnostics."""
        runner = self._find_runner(project_root)
        if not runner:
            return []

        config_path = self._resolve_config_path(project_root)
        cmd = [*runner, "--project", str(config_path), "--outputjson", *args]

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
            diagnostics = parsed.get("generalDiagnostics")
            return diagnostics if isinstance(diagnostics, list) else []
        except (OSError, subprocess.SubprocessError, json.JSONDecodeError) as err:
            logger.debug("Pyright execution encountered: %s", err)
            return []

    def _map_item_to_finding(
        self,
        item: dict[str, Any],
        project_root: Path,
    ) -> QualityFinding | None:
        """Translates a raw Pyright JSON diagnostic item into a QualityFinding."""
        file_path_str = str(item.get("file") or "").replace("\\", "/")
        try:
            rel_path = Path(file_path_str).relative_to(project_root).as_posix()
        except ValueError:
            rel_path = file_path_str

        # Skip vendor directories
        if "/.venv/" in f"/{rel_path}/" or "/node_modules/" in f"/{rel_path}/":
            return None

        range_info = item.get("range") or {}
        start_pt = range_info.get("start") or {}
        end_pt = range_info.get("end") or start_pt
        start_line = int(start_pt.get("line") or 0) + 1
        end_line = int(end_pt.get("line") or start_line - 1) + 1
        message = str(item.get("message") or "")
        pyright_rule = str(item.get("rule") or "reportGeneralTypeIssues")
        severity_str = str(item.get("severity") or "warning").lower()

        # Categorize rule into Sonar-equivalent concepts
        lower_msg = message.lower()
        if "was never awaited" in lower_msg or "coroutine" in lower_msg:
            rule_id = "python:S6544"
            rspec_id = "RSPEC-6544"
            category = QualityCategory.RELIABILITY
            severity = QualitySeverity.HIGH
            title = "Coroutines and Promises must be awaited or handled."
        elif "is deprecated" in lower_msg:
            rule_id = "python:S1874"
            rspec_id = "RSPEC-1874"
            category = QualityCategory.MAINTAINABILITY
            severity = QualitySeverity.MEDIUM
            title = "Deprecated API or symbol usage should be replaced."
        else:
            rule_id = f"python:{pyright_rule}"
            rspec_id = f"PYRIGHT-{pyright_rule}"
            category = (
                QualityCategory.RELIABILITY
                if severity_str == "error"
                else QualityCategory.CODE_SMELL
            )
            severity = QualitySeverity.HIGH if severity_str == "error" else QualitySeverity.MEDIUM
            title = f"Type Check: {message.splitlines()[0][:80]}"

        raw_id = f"{rel_path}:{start_line}:{rule_id}:{message}"
        fid = f"QUAL-TYPE-{hashlib.sha256(raw_id.encode('utf-8')).hexdigest()[:12]}"
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
            compliant_snippet=None,
            remediation_guidance="Resolve type conflict or annotate with specific type.",
            cwe_ids=[],
            owasp_category=None,
        )

    def audit_file(self, file_path: Path, project_root: Path) -> list[QualityFinding]:
        """Audits a single Python file for semantic type issues."""
        if file_path.suffix.lower() != ".py":
            return []
        items = self._run_pyright([str(file_path)], project_root)
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
        """Audits target files or entire project root for type errors."""
        args: list[str] = []
        if file_list is not None:
            py_files = [str(p) for p in file_list if p.suffix.lower() == ".py"]
            if not py_files:
                return []
            args = py_files

        items = self._run_pyright(args, project_root)
        findings: list[QualityFinding] = []
        for item in items:
            f = self._map_item_to_finding(item, project_root)
            if f:
                findings.append(f)
        return findings
