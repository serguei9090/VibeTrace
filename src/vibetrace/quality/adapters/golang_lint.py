"""Production Go Linter Adapter for Quality & Cognitive Complexity Analysis.

Executes golangci-lint in subprocess with native JSON issue formatting, maps linter
rules (gocyclo, gocognit, errcheck, staticcheck, ineffassign) to canonical Sonar RSPEC
IDs, and emits standardized QualityFindings.
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

SUPPORTED_EXTENSIONS = frozenset({".go"})

# Canonical Sonar RSPEC mapping for common golangci-lint linters
GOLANG_TO_SONAR: dict[str, tuple[str, str, QualityCategory, QualitySeverity]] = {
    "gocyclo": ("S3776", "RSPEC-3776", QualityCategory.COMPLEXITY, QualitySeverity.HIGH),
    "gocognit": ("S3776", "RSPEC-3776", QualityCategory.COMPLEXITY, QualitySeverity.HIGH),
    "cyclop": ("S1541", "RSPEC-1541", QualityCategory.COMPLEXITY, QualitySeverity.MEDIUM),
    "errcheck": ("S2970", "RSPEC-2970", QualityCategory.RELIABILITY, QualitySeverity.HIGH),
    "ineffassign": ("S1854", "RSPEC-1854", QualityCategory.MAINTAINABILITY, QualitySeverity.LOW),
    "unused": ("S1481", "RSPEC-1481", QualityCategory.MAINTAINABILITY, QualitySeverity.LOW),
    "deadcode": ("S1144", "RSPEC-1144", QualityCategory.MAINTAINABILITY, QualitySeverity.MEDIUM),
    "govet": ("S2259", "RSPEC-2259", QualityCategory.RELIABILITY, QualitySeverity.HIGH),
    "staticcheck": ("S1874", "RSPEC-1874", QualityCategory.MAINTAINABILITY, QualitySeverity.MEDIUM),
    "revive": ("S1135", "RSPEC-1135", QualityCategory.CODE_SMELL, QualitySeverity.LOW),
    "nestif": ("S134", "RSPEC-134", QualityCategory.COMPLEXITY, QualitySeverity.MEDIUM),
    "dupl": ("S1192", "RSPEC-1192", QualityCategory.CODE_SMELL, QualitySeverity.LOW),
}


class GolangLintAdapter(LinterSubEngine):
    """Sub-engine adapter executing golangci-lint and normalizing findings to Sonar RSPEC."""

    name = "golangci-lint"
    SUPPORTED_EXTENSIONS = SUPPORTED_EXTENSIONS

    def __init__(self, timeout_sec: float = 30.0) -> None:
        self.timeout_sec = timeout_sec
        self._cached_runner: list[str] | None = None

    def _find_go_mod(self, project_root: Path) -> Path | None:
        """Locates go.mod at project root or subdirectories."""
        for cand in (project_root / "go.mod", project_root / "src" / "go.mod"):
            if cand.is_file():
                return cand
        return None

    def _resolve_config_path(self, project_root: Path) -> Path | None:
        """Resolves active .golangci.yml configuration."""
        for cand in (
            ".golangci.yml",
            ".golangci.yaml",
            ".golangci.toml",
            ".golangci.json",
        ):
            p = project_root / cand
            if p.is_file():
                return p
        return None

    def _find_runner(self, project_root: Path) -> list[str] | None:
        """Finds golangci-lint executable in PATH or workspace bin."""
        if self._cached_runner is not None:
            return self._cached_runner

        candidates: list[list[str]] = []
        is_win = os.name == "nt"
        exe_ext = ".exe" if is_win else ""

        # 1. System PATH
        which_gl = shutil.which("golangci-lint")
        if which_gl:
            candidates.append([which_gl])

        # 2. GOPATH / GOBIN
        gopath = os.environ.get("GOPATH")
        if gopath:
            bin_gl = Path(gopath) / "bin" / f"golangci-lint{exe_ext}"
            if bin_gl.is_file():
                candidates.append([str(bin_gl)])

        # 3. User home go/bin
        home_go_bin = Path.home() / "go" / "bin" / f"golangci-lint{exe_ext}"
        if home_go_bin.is_file():
            candidates.append([str(home_go_bin)])

        # 4. go run fallback if go exists
        which_go = shutil.which("go")
        if which_go:
            candidates.append(
                [which_go, "run", "github.com/golangci/golangci-lint/cmd/golangci-lint@latest"]
            )

        for cand in candidates:
            try:
                res = subprocess.run(
                    [*cand, "version"],
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
        """Returns True if golangci-lint or go is found and a go.mod or .go files exist."""
        has_runner = self._find_runner(project_root) is not None
        if not has_runner:
            return False
        if self._find_go_mod(project_root) is not None:
            return True
        return any(project_root.rglob("*.go"))

    def _map_issue_to_finding(
        self,
        issue: dict[str, Any],
        project_root: Path,
    ) -> QualityFinding | None:
        """Maps a single golangci-lint JSON issue to a QualityFinding."""
        linter = str(issue.get("FromLinter") or "revive").strip()
        pos = issue.get("Pos") or {}
        raw_file = str(pos.get("Filename") or "").replace("\\", "/")
        if not raw_file:
            return None

        file_path = (project_root / raw_file).resolve()
        try:
            rel_file = file_path.relative_to(project_root).as_posix()
        except ValueError:
            rel_file = raw_file

        start_line = int(pos.get("Line") or 1)
        text = str(issue.get("Text") or f"Issue flagged by {linter}").strip()

        rule_info = GOLANG_TO_SONAR.get(linter)
        if rule_info:
            s_code, rspec_id, category, severity = rule_info
            sonar_rule_id = f"go:{s_code}"
        else:
            s_code = "S1135"
            rspec_id = "RSPEC-1135"
            category = QualityCategory.CODE_SMELL
            severity = QualitySeverity.LOW
            sonar_rule_id = f"go:{linter}"

        hash_id = hashlib.sha256(f"{rel_file}:{start_line}:{linter}:{text}".encode()).hexdigest()[
            :12
        ]

        finding = QualityFinding(
            id=f"QUAL-{s_code}-{hash_id}",
            rule_id=sonar_rule_id,
            rspec_id=rspec_id,
            category=category,
            severity=severity,
            title=f"Go {linter}: {text[:80]}",
            message=text,
            file_path=rel_file,
            start_line=start_line,
            end_line=start_line,
            code_hash=hash_id,
            remediation_guidance=f"Address {linter} finding in accordance with Go best practices.",
        )

        spec = get_rule_spec(resolve_canonical_rule_id(sonar_rule_id))
        if spec:
            finding.title = spec.title
            if spec.explanation:
                finding.remediation_guidance = spec.explanation
            if spec.compliant_example:
                finding.compliant_snippet = spec.get_compliant_example("go")
        return finding

    def _run_golangci_lint(self, project_root: Path, file_args: list[str]) -> list[dict[str, Any]]:
        """Executes golangci-lint with JSON output format."""
        runner = self._find_runner(project_root)
        if not runner:
            return []

        config_path = self._resolve_config_path(project_root)
        config_arg = (
            ["--config", str(config_path)] if config_path else ["--enable=gocyclo,errcheck,revive"]
        )
        cmd = [*runner, "run", "--out-format=json", *config_arg, *file_args]

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
            logger.debug("Failed to execute golangci-lint: %s", err)
            return []

        output = res.stdout.strip()
        if not output:
            return []

        try:
            parsed = json.loads(output)
            if (
                isinstance(parsed, dict)
                and "Issues" in parsed
                and isinstance(parsed["Issues"], list)
            ):
                return parsed["Issues"]
        except json.JSONDecodeError:
            pass

        return []

    def audit_file(self, file_path: Path, project_root: Path) -> list[QualityFinding]:
        """Audits a single target Go file."""
        if file_path.suffix.lower() != ".go":
            return []
        return self.audit_project(project_root, [file_path])

    def audit_project(
        self,
        project_root: Path,
        file_list: list[Path] | None = None,
    ) -> list[QualityFinding]:
        """Audits target files or entire Go package/workspace."""
        target_rel_paths: set[str] | None = None
        if file_list is not None:
            rel_files = []
            target_rel_paths = set()
            for p in file_list:
                try:
                    rel_p = p.relative_to(project_root).as_posix()
                except ValueError:
                    rel_p = str(p).replace("\\", "/")
                target_rel_paths.add(rel_p)
                rel_files.append(rel_p)
            file_args = rel_files if rel_files else ["./..."]
        else:
            file_args = ["./..."]

        issues = self._run_golangci_lint(project_root, file_args)
        findings: list[QualityFinding] = []
        for iss in issues:
            f = self._map_issue_to_finding(iss, project_root)
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
        """Runs golangci-lint run --fix on target Go files."""
        runner = self._find_runner(project_root)
        if not runner:
            return {
                "engine": "golangci-lint",
                "available": False,
                "success": False,
                "files_count": 0,
            }

        config_path = self._resolve_config_path(project_root)
        config_arg = ["--config", str(config_path)] if config_path else []
        cmd = [*runner, "run", "--fix", *config_arg, "./..."]

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
                "engine": "golangci-lint",
                "available": True,
                "success": res.returncode in (0, 1),
                "output": res.stdout.strip(),
            }
        except (OSError, subprocess.SubprocessError) as err:
            logger.debug("golangci-lint fix execution error: %s", err)
            return {
                "engine": "golangci-lint",
                "available": True,
                "success": False,
                "error": str(err),
            }
