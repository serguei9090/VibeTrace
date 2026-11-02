"""Production Rust Cargo Clippy Adapter for Quality & Cognitive Complexity Analysis.

Executes cargo clippy in subprocess with native JSON compiler messages, maps Clippy
lint codes (clippy::cognitive_complexity, clippy::collapsible_if, etc.) to canonical
Sonar RSPEC IDs, and emits standardized QualityFindings.
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

SUPPORTED_EXTENSIONS = frozenset({".rs"})

CLIPPY_TO_SONAR: dict[str, tuple[str, str, QualityCategory, QualitySeverity]] = {
    "clippy::cognitive_complexity": (
        "S3776",
        "RSPEC-3776",
        QualityCategory.COMPLEXITY,
        QualitySeverity.HIGH,
    ),
    "clippy::cyclomatic_complexity": (
        "S1541",
        "RSPEC-1541",
        QualityCategory.COMPLEXITY,
        QualitySeverity.MEDIUM,
    ),
    "clippy::too_many_arguments": (
        "S107",
        "RSPEC-107",
        QualityCategory.MAINTAINABILITY,
        QualitySeverity.MEDIUM,
    ),
    "clippy::collapsible_if": (
        "S1066",
        "RSPEC-1066",
        QualityCategory.CODE_SMELL,
        QualitySeverity.LOW,
    ),
    "clippy::collapsible_else_if": (
        "S1066",
        "RSPEC-1066",
        QualityCategory.CODE_SMELL,
        QualitySeverity.LOW,
    ),
    "clippy::needless_return": (
        "S3626",
        "RSPEC-3626",
        QualityCategory.CODE_SMELL,
        QualitySeverity.LOW,
    ),
    "clippy::redundant_clone": (
        "S2159",
        "RSPEC-2159",
        QualityCategory.MAINTAINABILITY,
        QualitySeverity.MEDIUM,
    ),
    "clippy::clone_on_copy": (
        "S2159",
        "RSPEC-2159",
        QualityCategory.MAINTAINABILITY,
        QualitySeverity.LOW,
    ),
    "clippy::unwrap_used": (
        "S2970",
        "RSPEC-2970",
        QualityCategory.RELIABILITY,
        QualitySeverity.HIGH,
    ),
    "clippy::expect_used": (
        "S2970",
        "RSPEC-2970",
        QualityCategory.RELIABILITY,
        QualitySeverity.MEDIUM,
    ),
    "clippy::panic": (
        "S2970",
        "RSPEC-2970",
        QualityCategory.RELIABILITY,
        QualitySeverity.HIGH,
    ),
    "dead_code": (
        "S1144",
        "RSPEC-1144",
        QualityCategory.MAINTAINABILITY,
        QualitySeverity.MEDIUM,
    ),
    "unused_variables": (
        "S1481",
        "RSPEC-1481",
        QualityCategory.MAINTAINABILITY,
        QualitySeverity.LOW,
    ),
    "unused_mut": (
        "S2933",
        "RSPEC-2933",
        QualityCategory.MAINTAINABILITY,
        QualitySeverity.LOW,
    ),
    "clippy::double_neg": (
        "S2589",
        "RSPEC-2589",
        QualityCategory.CODE_SMELL,
        QualitySeverity.LOW,
    ),
    "clippy::match_single_binding": (
        "S1301",
        "RSPEC-1301",
        QualityCategory.CODE_SMELL,
        QualitySeverity.LOW,
    ),
}


class CargoClippyAdapter(LinterSubEngine):
    """Sub-engine adapter executing Cargo Clippy and normalizing to Sonar RSPEC findings."""

    name = "clippy"
    SUPPORTED_EXTENSIONS = SUPPORTED_EXTENSIONS

    def __init__(self, timeout_sec: float = 30.0) -> None:
        self.timeout_sec = timeout_sec
        self._cached_runner: list[str] | None = None

    def _find_cargo_toml(self, project_root: Path) -> Path | None:
        """Locates Cargo.toml at project root or subdirectories."""
        for cand in (project_root / "Cargo.toml", project_root / "src" / "Cargo.toml"):
            if cand.is_file():
                return cand
        return None

    def _find_runner(self, project_root: Path) -> list[str] | None:
        """Finds cargo executable in workspace or system PATH."""
        if self._cached_runner is not None:
            return self._cached_runner

        candidates: list[list[str]] = []
        cargo_path = shutil.which("cargo")
        if cargo_path:
            candidates.append([cargo_path])

        home_cargo = Path.home() / ".cargo" / "bin" / ("cargo.exe" if os.name == "nt" else "cargo")
        if home_cargo.is_file():
            candidates.append([str(home_cargo)])

        for cand in candidates:
            try:
                res = subprocess.run(
                    [*cand, "--version"],
                    capture_output=True,
                    text=True,
                    check=False,
                    timeout=5.0,
                )
                if res.returncode == 0:
                    self._cached_runner = cand
                    return self._cached_runner
            except (OSError, subprocess.SubprocessError):
                continue

        return None

    def is_available(self, project_root: Path) -> bool:
        """Returns True if cargo runner is found and a Cargo.toml exists."""
        return (
            self._find_runner(project_root) is not None
            and self._find_cargo_toml(project_root) is not None
        )

    def _map_compiler_message(
        self,
        msg: dict[str, Any],
        project_root: Path,
    ) -> QualityFinding | None:
        """Maps a single cargo clippy compiler message to a QualityFinding."""
        code_obj = msg.get("code")
        raw_code = code_obj.get("code") if isinstance(code_obj, dict) else None
        if not raw_code:
            return None

        spans = msg.get("spans") or []
        primary_span = next((s for s in spans if s.get("is_primary")), None)
        if not primary_span and spans:
            primary_span = spans[0]
        if not primary_span:
            return None

        file_name = primary_span.get("file_name") or ""
        file_path = (project_root / file_name).resolve()
        try:
            rel_file = file_path.relative_to(project_root).as_posix()
        except ValueError:
            rel_file = str(file_name).replace("\\", "/")

        start_line = int(primary_span.get("line_start") or 1)
        end_line = int(primary_span.get("line_end") or start_line)

        rule_info = CLIPPY_TO_SONAR.get(raw_code)
        if rule_info:
            s_code, rspec_id, category, severity = rule_info
            sonar_rule_id = f"rust:{s_code}"
        else:
            s_code = "S1135"
            rspec_id = "RSPEC-1135"
            category = QualityCategory.CODE_SMELL
            severity = QualitySeverity.LOW
            sonar_rule_id = f"rust:{raw_code.replace('::', '-')}"

        rendered = msg.get("rendered") or ""
        rendered_snippet = None
        if rendered:
            lines = [
                line_str
                for line_str in rendered.splitlines()
                if "|" in line_str and not line_str.strip().startswith("-->")
            ]
            if lines:
                rendered_snippet = "\n".join(lines[:4])

        message_text = msg.get("message") or f"Clippy warning: {raw_code}"
        hash_id = hashlib.sha256(f"{rel_file}:{start_line}:{raw_code}".encode()).hexdigest()[:12]

        finding = QualityFinding(
            id=f"QUAL-{s_code}-{hash_id}",
            rule_id=sonar_rule_id,
            rspec_id=rspec_id,
            category=category,
            severity=severity,
            title=f"Rust {raw_code}: {message_text[:80]}",
            message=message_text,
            file_path=rel_file,
            start_line=start_line,
            end_line=end_line,
            code_hash=hash_id,
            non_compliant_snippet=rendered_snippet,
            remediation_guidance=(
                f"Consult Clippy documentation for {raw_code} and address compiler diagnostic."
            ),
        )

        spec = get_rule_spec(resolve_canonical_rule_id(sonar_rule_id))
        if spec:
            finding.title = spec.title
            if spec.explanation:
                finding.remediation_guidance = spec.explanation
            if spec.compliant_example:
                finding.compliant_snippet = spec.get_compliant_example("rust")
        return finding

    def _run_clippy(self, project_root: Path) -> list[dict[str, Any]]:
        """Executes cargo clippy with JSON message output format."""
        runner = self._find_runner(project_root)
        if not runner:
            return []

        cargo_toml = self._find_cargo_toml(project_root)
        manifest_arg = ["--manifest-path", str(cargo_toml)] if cargo_toml else []
        cmd = [*runner, "clippy", *manifest_arg, "--message-format=json", "--", "-D", "warnings"]

        try:
            res = subprocess.run(
                cmd,
                cwd=str(project_root),
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                check=False,
                timeout=self.timeout_sec,
            )
        except (OSError, subprocess.SubprocessError) as err:
            logger.debug("Failed to execute cargo clippy: %s", err)
            return []

        messages: list[dict[str, Any]] = []
        for line in res.stdout.splitlines():
            line = line.strip()
            if not line.startswith("{"):
                continue
            try:
                data = json.loads(line)
                if data.get("reason") == "compiler-message" and "message" in data:
                    messages.append(data["message"])
            except json.JSONDecodeError:
                continue

        return messages

    def audit_file(self, file_path: Path, project_root: Path) -> list[QualityFinding]:
        """Audits a single target Rust file."""
        if file_path.suffix.lower() != ".rs":
            return []
        all_findings = self.audit_project(project_root, [file_path])
        return all_findings

    def audit_project(
        self,
        project_root: Path,
        file_list: list[Path] | None = None,
    ) -> list[QualityFinding]:
        """Audits target files or entire crate using Cargo Clippy."""
        messages = self._run_clippy(project_root)
        findings: list[QualityFinding] = []

        target_rel_paths: set[str] | None = None
        if file_list is not None:
            target_rel_paths = set()
            for p in file_list:
                try:
                    target_rel_paths.add(p.relative_to(project_root).as_posix())
                except ValueError:
                    target_rel_paths.add(str(p).replace("\\", "/"))

        for msg in messages:
            f = self._map_compiler_message(msg, project_root)
            if f:
                if target_rel_paths is not None and f.file_path not in target_rel_paths:
                    continue
                findings.append(f)

        return findings

    def fix_files(
        self,
        project_root: Path,
        file_list: list[Path] | None = None,
    ) -> dict[str, Any]:
        """Runs cargo clippy --fix --allow-dirty on target crate."""
        runner = self._find_runner(project_root)
        if not runner or not self._find_cargo_toml(project_root):
            return {"engine": "clippy", "available": False, "success": False, "files_count": 0}

        cargo_toml = self._find_cargo_toml(project_root)
        manifest_arg = ["--manifest-path", str(cargo_toml)] if cargo_toml else []
        cmd = [*runner, "clippy", "--fix", "--allow-dirty", "--allow-staged", *manifest_arg]

        try:
            res = subprocess.run(
                cmd,
                cwd=str(project_root),
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                check=False,
                timeout=self.timeout_sec * 2,
            )
            return {
                "engine": "clippy",
                "available": True,
                "success": res.returncode == 0,
                "output": res.stdout.strip(),
            }
        except (OSError, subprocess.SubprocessError) as err:
            logger.debug("Cargo clippy fix execution error: %s", err)
            return {"engine": "clippy", "available": True, "success": False, "error": str(err)}
