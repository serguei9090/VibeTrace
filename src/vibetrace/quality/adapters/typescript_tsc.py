"""Production TypeScript Compiler (tsc) Sub-Engine Adapter with Incremental Caching.

Executes `tsc --noEmit --incremental` to perform deep type-checking and deprecation
resolution across packages, mapping diagnostic codes (TS6385, TS2322, TS2531)
to canonical Sonar RSPEC standards.
"""

from __future__ import annotations

import hashlib
import logging
import os
import re
import shutil
import subprocess
from pathlib import Path

from vibetrace.quality.adapters.base import LinterSubEngine
from vibetrace.quality.models import QualityCategory, QualityFinding, QualitySeverity

logger = logging.getLogger(__name__)

SUPPORTED_EXTENSIONS = frozenset({".ts", ".tsx", ".mts", ".cts"})

_DIAG_RE = re.compile(r"^(.+?)\((\d+),(\d+)\):\s+(error|warning)\s+TS(\d+):\s+(.+)$")

TSC_TO_SONAR: dict[str, tuple[str, str, QualityCategory, QualitySeverity]] = {
    # @deprecated symbol usages (S1874)
    "6385": ("S1874", "RSPEC-1874", QualityCategory.CODE_SMELL, QualitySeverity.MEDIUM),
    "6387": ("S1874", "RSPEC-1874", QualityCategory.CODE_SMELL, QualitySeverity.MEDIUM),
    # Type mismatches and assignments (S4335)
    "2322": ("S4335", "RSPEC-4335", QualityCategory.RELIABILITY, QualitySeverity.HIGH),
    "2345": ("S4335", "RSPEC-4335", QualityCategory.RELIABILITY, QualitySeverity.HIGH),
    # Possibly undefined / null dereferencing (S2259)
    "2531": ("S2259", "RSPEC-2259", QualityCategory.RELIABILITY, QualitySeverity.HIGH),
    "2532": ("S2259", "RSPEC-2259", QualityCategory.RELIABILITY, QualitySeverity.HIGH),
    # Implicit any parameter (S6548)
    "7006": ("S6548", "RSPEC-6548", QualityCategory.CODE_SMELL, QualitySeverity.LOW),
    # Missing property access (S3785)
    "2339": ("S3785", "RSPEC-3785", QualityCategory.RELIABILITY, QualitySeverity.HIGH),
}


class TypeScriptAdapter(LinterSubEngine):
    """Sub-engine adapter executing tsc with incremental caching and Sonar code translation."""

    name = "tsc"
    SUPPORTED_EXTENSIONS = SUPPORTED_EXTENSIONS

    def __init__(self, timeout_sec: float = 30.0) -> None:
        self.timeout_sec = timeout_sec
        self._cached_runner: list[str] | None = None

    def _find_runner(self, project_root: Path) -> list[str] | None:
        """Determines working executable runner for tsc in workspace or environment."""
        if self._cached_runner is not None:
            return self._cached_runner

        is_win = os.name == "nt"
        cmd_ext = ".cmd" if is_win else ""
        candidates: list[list[str]] = []

        for sub in ("", "src/web", "web", "frontend"):
            local_bin = project_root / sub / "node_modules" / ".bin" / f"tsc{cmd_ext}"
            if local_bin.is_file():
                candidates.append([str(local_bin)])

        which_tsc = shutil.which("tsc")
        if which_tsc:
            candidates.append([which_tsc])

        if shutil.which("bunx"):
            candidates.append(["bunx", "tsc"])
        elif shutil.which("bun"):
            candidates.append(["bun", "x", "tsc"])

        if shutil.which("npx"):
            candidates.append(["npx", "--yes", "tsc"])

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

    def _find_tsconfig(self, project_root: Path) -> Path | None:
        """Locates project or workspace tsconfig.json."""
        candidates = (
            project_root / "tsconfig.json",
            project_root / "src" / "web" / "tsconfig.app.json",
            project_root / "src" / "web" / "tsconfig.json",
            project_root / "web" / "tsconfig.json",
        )
        for cand in candidates:
            if cand.is_file():
                return cand
        return None

    def _resolve_config_path(self, project_root: Path) -> Path | None:
        """Resolves active tsconfig.json based on configuration."""
        from vibetrace.config import resolve_linter_mode

        mode = resolve_linter_mode(project_root, "typescript")
        if mode == "project":
            cfg = self._find_tsconfig(project_root)
            if cfg:
                return cfg

        override = project_root / ".vibetrace" / "config" / "tsconfig.json"
        if override.is_file():
            return override
        return self._find_tsconfig(project_root)

    def is_available(self, project_root: Path) -> bool:
        """Returns True if tsc runner and tsconfig are available."""
        return (
            self._find_runner(project_root) is not None
            and self._find_tsconfig(project_root) is not None
        )

    def _parse_tsc_output(
        self,
        output: str,
        project_root: Path,
        target_files: set[str] | None = None,
    ) -> list[QualityFinding]:
        """Parses raw tsc stdout diagnostics into standardized QualityFinding records."""
        findings: list[QualityFinding] = []
        for line in output.splitlines():
            line = line.strip()
            match = _DIAG_RE.match(line)
            if not match:
                continue

            raw_file, s_line_str, _, level_str, ts_code, message = match.groups()
            s_line = int(s_line_str)
            p = Path(raw_file)
            try:
                rel_path = (
                    p.resolve().relative_to(project_root.resolve()).as_posix()
                    if p.is_absolute()
                    else p.as_posix()
                )
            except ValueError:
                rel_path = p.as_posix()

            if target_files and rel_path not in target_files:
                continue

            if ts_code in TSC_TO_SONAR:
                s_code, rspec, cat, sev = TSC_TO_SONAR[ts_code]
            else:
                s_code = f"TS{ts_code}"
                rspec = f"RSPEC-TS-{ts_code}"
                cat = QualityCategory.RELIABILITY
                sev = QualitySeverity.HIGH if level_str == "error" else QualitySeverity.MEDIUM

            raw_id = f"{rel_path}:{s_line}:TS{ts_code}"
            hash_suffix = hashlib.sha256(raw_id.encode("utf-8")).hexdigest()[:12]
            finding_id = f"QUAL-TSC-{hash_suffix}"
            code_hash = hashlib.sha256(f"{rel_path}:{s_line}:{message}".encode()).hexdigest()

            findings.append(
                QualityFinding(
                    id=finding_id,
                    rule_id=f"typescript:TS{ts_code}",
                    rspec_id=rspec,
                    category=cat,
                    severity=sev,
                    title=f"[{s_code}] {message}",
                    message=message,
                    file_path=rel_path,
                    start_line=s_line,
                    end_line=s_line,
                    symbol_name=None,
                    code_hash=code_hash,
                    remediation_guidance=(
                        f"Refer to TypeScript compiler diagnostic TS{ts_code} documentation."
                    ),
                )
            )

        return findings

    def audit_file(self, file_path: Path, project_root: Path) -> list[QualityFinding]:
        """Audits a single TypeScript file."""
        return self.audit_project(project_root, [file_path])

    def audit_project(
        self,
        project_root: Path,
        file_list: list[Path] | None = None,
    ) -> list[QualityFinding]:
        """Audits TypeScript project with fast incremental build cache."""
        runner = self._find_runner(project_root)
        tsconfig = self._find_tsconfig(project_root)
        if not runner or not tsconfig:
            return []

        cache_dir = project_root / ".vibetrace" / "cache"
        cache_dir.mkdir(parents=True, exist_ok=True)
        tsbuildinfo = cache_dir / "tsc.tsbuildinfo"

        cmd = [
            *runner,
            "--project",
            str(tsconfig),
            "--noEmit",
            "--incremental",
            "--tsBuildInfoFile",
            str(tsbuildinfo),
        ]

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
            logger.debug("Failed to execute tsc: %s", err)
            return []

        target_set: set[str] | None = None
        if file_list is not None:
            target_set = set()
            for f in file_list:
                try:
                    target_set.add(f.resolve().relative_to(project_root.resolve()).as_posix())
                except ValueError:
                    target_set.add(f.as_posix())

        return self._parse_tsc_output(res.stdout, project_root, target_files=target_set)
