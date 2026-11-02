"""Production Biome Adapter for JavaScript, TypeScript, JSX, JSON, and CSS Quality Analysis.

Executes Biome in subprocess with native SARIF output, maps Biome lint rules
(noGlobalEval, noDoubleEquals, useExhaustiveDependencies) to canonical Sonar RSPEC IDs,
and emits standardized QualityFindings.
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

BUNDLED_PRESET_PATH = (
    Path(__file__).parent.parent.parent / "resources" / "presets" / "biome_sonar.json"
)

SUPPORTED_EXTENSIONS = frozenset(
    {
        ".js",
        ".mjs",
        ".cjs",
        ".jsx",
        ".ts",
        ".mts",
        ".cts",
        ".tsx",
        ".json",
        ".jsonc",
        ".css",
    }
)

# Canonical Sonar RSPEC mapping for Biome lint rules
BIOME_TO_SONAR: dict[str, tuple[str, str, QualityCategory, QualitySeverity]] = {
    "lint/security/noGlobalEval": (
        "S1523",
        "RSPEC-1523",
        QualityCategory.RELIABILITY,
        QualitySeverity.HIGH,
    ),
    "lint/security/noDangerouslySetInnerHtml": (
        "S5147",
        "RSPEC-5147",
        QualityCategory.RELIABILITY,
        QualitySeverity.HIGH,
    ),
    "lint/suspicious/noDoubleEquals": (
        "S1444",
        "RSPEC-1444",
        QualityCategory.CODE_SMELL,
        QualitySeverity.MEDIUM,
    ),
    "lint/suspicious/noDebugger": (
        "S2703",
        "RSPEC-2703",
        QualityCategory.RELIABILITY,
        QualitySeverity.HIGH,
    ),
    "lint/correctness/noUnusedVariables": (
        "S1481",
        "RSPEC-1481",
        QualityCategory.MAINTAINABILITY,
        QualitySeverity.LOW,
    ),
    "lint/correctness/useExhaustiveDependencies": (
        "S6440",
        "RSPEC-6440",
        QualityCategory.RELIABILITY,
        QualitySeverity.MEDIUM,
    ),
    "lint/complexity/noExtraBooleanCast": (
        "S1940",
        "RSPEC-1940",
        QualityCategory.COMPLEXITY,
        QualitySeverity.LOW,
    ),
    "lint/suspicious/noAssignInExpressions": (
        "S1121",
        "RSPEC-1121",
        QualityCategory.CODE_SMELL,
        QualitySeverity.MEDIUM,
    ),
    "lint/correctness/noUnreachable": (
        "S1763",
        "RSPEC-1763",
        QualityCategory.CODE_SMELL,
        QualitySeverity.MEDIUM,
    ),
    "lint/suspicious/noDuplicateParameters": (
        "S1199",
        "RSPEC-1199",
        QualityCategory.RELIABILITY,
        QualitySeverity.HIGH,
    ),
    "lint/suspicious/noAsyncPromiseExecutor": (
        "S6544",
        "RSPEC-6544",
        QualityCategory.RELIABILITY,
        QualitySeverity.HIGH,
    ),
    "lint/suspicious/noExplicitAny": (
        "S6548",
        "RSPEC-6548",
        QualityCategory.CODE_SMELL,
        QualitySeverity.LOW,
    ),
    "lint/suspicious/noConsole": (
        "S2228",
        "RSPEC-2228",
        QualityCategory.MAINTAINABILITY,
        QualitySeverity.LOW,
    ),
    "lint/suspicious/noCatchAssign": (
        "S2137",
        "RSPEC-2137",
        QualityCategory.CODE_SMELL,
        QualitySeverity.HIGH,
    ),
    "lint/correctness/noConstantCondition": (
        "S2589",
        "RSPEC-2589",
        QualityCategory.RELIABILITY,
        QualitySeverity.MEDIUM,
    ),
    "lint/correctness/noEmptyPattern": (
        "S3699",
        "RSPEC-3699",
        QualityCategory.CODE_SMELL,
        QualitySeverity.MEDIUM,
    ),
    "lint/style/noVar": (
        "S3504",
        "RSPEC-3504",
        QualityCategory.MAINTAINABILITY,
        QualitySeverity.LOW,
    ),
    "lint/style/noNestedTernary": (
        "S3358",
        "RSPEC-3358",
        QualityCategory.CODE_SMELL,
        QualitySeverity.MEDIUM,
    ),
    "lint/complexity/noExcessiveCognitiveComplexity": (
        "S3776",
        "RSPEC-3776",
        QualityCategory.COMPLEXITY,
        QualitySeverity.HIGH,
    ),
    "lint/complexity/useMaxParams": (
        "S107",
        "RSPEC-107",
        QualityCategory.CODE_SMELL,
        QualitySeverity.LOW,
    ),
    "lint/style/useForOf": (
        "S7781",
        "RSPEC-7781",
        QualityCategory.CODE_SMELL,
        QualitySeverity.LOW,
    ),
    "lint/style/useReadonlyClassProperties": (
        "S2933",
        "RSPEC-2933",
        QualityCategory.CODE_SMELL,
        QualitySeverity.LOW,
    ),
}


class BiomeAdapter(LinterSubEngine):
    """Sub-engine adapter executing Biome and normalizing findings to Sonar RSPEC standards."""

    name = "biome"
    SUPPORTED_EXTENSIONS = SUPPORTED_EXTENSIONS

    def __init__(self, timeout_sec: float = 20.0) -> None:
        self.timeout_sec = timeout_sec
        self._cached_runner: list[str] | None = None

    def _resolve_config_path(self, project_root: Path) -> Path:
        """Resolves configuration based on user settings in .vibetrace.toml."""
        from vibetrace.config import resolve_linter_mode

        mode = resolve_linter_mode(project_root, "biome")
        if mode == "project":
            for cand in ("biome.json", "biome.jsonc"):
                p = project_root / cand
                if p.is_file():
                    return p

        override = project_root / ".vibetrace" / "config" / "biome.json"
        if override.is_file():
            return override
        return BUNDLED_PRESET_PATH

    def _find_runner(self, project_root: Path) -> list[str] | None:
        """Determines the best working executable runner for Biome in the workspace."""
        if self._cached_runner is not None:
            return self._cached_runner

        candidates: list[list[str]] = []
        is_win = os.name == "nt"
        cmd_ext = ".cmd" if is_win else ""
        exe_ext = ".exe" if is_win else ""

        # 1. Local node_modules/.bin/biome
        node_bin = project_root / "node_modules" / ".bin" / f"biome{cmd_ext}"
        if node_bin.is_file():
            candidates.append([str(node_bin)])

        # 2. Standalone cached binary in ~/.vibetrace/bin/biome
        user_cached = Path.home() / ".vibetrace" / "bin" / f"biome{exe_ext}"
        if user_cached.is_file():
            candidates.append([str(user_cached)])

        # 3. Global biome on PATH
        which_biome = shutil.which("biome")
        if which_biome:
            candidates.append([which_biome])

        # 4. bunx @biomejs/biome
        if shutil.which("bunx"):
            candidates.append(["bunx", "@biomejs/biome"])
        elif shutil.which("bun"):
            candidates.append(["bun", "x", "@biomejs/biome"])

        # 5. npx @biomejs/biome
        if shutil.which("npx"):
            candidates.append(["npx", "--yes", "@biomejs/biome"])

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
        """Returns True if a working Biome runner is available."""
        return self._find_runner(project_root) is not None

    def _run_biome(self, args: list[str], project_root: Path) -> list[dict[str, Any]]:
        """Executes Biome with SARIF output formatting and returns parsed results."""
        runner = self._find_runner(project_root)
        if not runner:
            return []

        config_path = self._resolve_config_path(project_root)
        config_arg = f"--config-path={config_path}" if config_path.is_file() else None
        cmd = [*runner, "lint"]
        if config_arg:
            cmd.append(config_arg)
        cmd.extend(["--reporter=sarif", "--no-errors-on-unmatched", *args])
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
            logger.debug("Failed to execute Biome process: %s", err)
            return []

        # stdout contains pure SARIF json
        output = res.stdout.strip()
        if not output:
            return []

        try:
            sarif = json.loads(extract_json_payload(output))
            runs = sarif.get("runs") or []
            if runs and isinstance(runs, list) and isinstance(runs[0], dict):
                return runs[0].get("results") or []
        except (json.JSONDecodeError, ValueError) as err:
            logger.debug("Failed to parse Biome SARIF output: %s", err)

        return []

    def _map_sarif_result(self, res: dict[str, Any], project_root: Path) -> QualityFinding | None:
        """Converts an individual Biome SARIF result into a standardized QualityFinding."""
        rule_id = str(res.get("ruleId") or "")
        if not rule_id:
            return None

        # Resolve Sonar mapping
        if rule_id in BIOME_TO_SONAR:
            s_code, rspec, cat, sev = BIOME_TO_SONAR[rule_id]
        elif rule_id.startswith("lint/a11y/"):
            s_code, rspec, cat, sev = (
                "S6848",
                "RSPEC-6848",
                QualityCategory.CODE_SMELL,
                QualitySeverity.MEDIUM,
            )
        else:
            canonical = resolve_canonical_rule_id(rule_id)
            spec = get_rule_spec(canonical)
            if spec:
                s_code = spec.rule_id
                rspec = spec.rspec_id
                cat = spec.category
                sev = spec.severity
            else:
                s_code = rule_id.split("/")[-1]
                rspec = f"RSPEC-BIOME-{s_code}"
                if rule_id.startswith("lint/security/"):
                    cat = QualityCategory.RELIABILITY
                    sev = QualitySeverity.HIGH
                elif rule_id.startswith("lint/correctness/"):
                    cat = QualityCategory.RELIABILITY
                    sev = QualitySeverity.MEDIUM
                elif rule_id.startswith("lint/complexity/"):
                    cat = QualityCategory.COMPLEXITY
                    sev = QualitySeverity.LOW
                else:
                    cat = QualityCategory.CODE_SMELL
                    sev = QualitySeverity.LOW

        locations = res.get("locations") or []
        if not locations or not isinstance(locations, list):
            return None

        first_loc = locations[0].get("physicalLocation") or {}
        uri_str = str(first_loc.get("artifactLocation", {}).get("uri") or "").replace("\\", "/")
        if not uri_str:
            return None

        try:
            rel_path = Path(uri_str).relative_to(project_root).as_posix()
        except ValueError:
            rel_path = uri_str.lstrip("/")

        region = first_loc.get("region") or {}
        start_line = int(region.get("startLine") or 1)
        end_line = int(region.get("endLine") or start_line)

        msg_obj = res.get("message") or {}
        message = str(msg_obj.get("text") or rule_id)
        raw_key = f"{rel_path}:{start_line}:{rule_id}"
        hash_suffix = hashlib.sha256(raw_key.encode("utf-8")).hexdigest()[:12]
        finding_id = f"QUAL-BIOME-{hash_suffix}"
        code_hash = hashlib.sha256(f"{rel_path}:{start_line}:{message}".encode()).hexdigest()

        return QualityFinding(
            id=finding_id,
            rule_id=f"biome:{rule_id}",
            rspec_id=rspec,
            category=cat,
            severity=sev,
            title=f"[{s_code}] {message}",
            message=message,
            file_path=rel_path,
            start_line=start_line,
            end_line=end_line,
            symbol_name=None,
            code_hash=code_hash,
            remediation_guidance=f"Refer to Biome documentation for rule `{rule_id}`.",
        )

    def audit_file(self, file_path: Path, project_root: Path) -> list[QualityFinding]:
        """Audits a single file with Biome."""
        if file_path.suffix.lower() not in SUPPORTED_EXTENSIONS:
            return []
        try:
            rel_str = file_path.relative_to(project_root).as_posix()
        except ValueError:
            rel_str = str(file_path)

        results = self._run_biome(["--vcs-use-ignore-file=false", rel_str], project_root)
        findings: list[QualityFinding] = []
        for r in results:
            f = self._map_sarif_result(r, project_root)
            if f:
                findings.append(f)
        return findings

    def audit_project(
        self,
        project_root: Path,
        file_list: list[Path] | None = None,
    ) -> list[QualityFinding]:
        """Audits target files or the entire project using batch-optimized sub-processes."""
        if file_list is not None:
            targets = [p for p in file_list if p.suffix.lower() in SUPPORTED_EXTENSIONS]
            if not targets:
                return []
            chunks: list[list[Path]] = [targets[i : i + 50] for i in range(0, len(targets), 50)]
        else:
            chunks = [[Path(".")]]

        findings: list[QualityFinding] = []
        for chunk in chunks:
            args: list[str] = ["--vcs-use-ignore-file=false"] if file_list is not None else []
            for p in chunk:
                try:
                    rel_str = p.relative_to(project_root).as_posix()
                except ValueError:
                    rel_str = str(p)
                args.append(rel_str)

            results = self._run_biome(args, project_root)
            for r in results:
                f = self._map_sarif_result(r, project_root)
                if f:
                    findings.append(f)

        return findings

    def fix_files(
        self,
        project_root: Path,
        file_list: list[Path] | None = None,
    ) -> dict[str, Any]:
        """Runs biome check --write on target files or project root."""
        runner = self._find_runner(project_root)
        if not runner:
            return {"engine": "biome", "available": False, "success": False, "files_count": 0}

        config_path = self._resolve_config_path(project_root)
        config_arg = f"--config-path={config_path}" if config_path.is_file() else None
        cmd = [*runner, "check", "--write", "--no-errors-on-unmatched"]
        if config_arg:
            cmd.append(config_arg)

        if file_list is not None:
            targets = [p for p in file_list if p.suffix.lower() in SUPPORTED_EXTENSIONS]
            if not targets:
                return {"engine": "biome", "available": True, "success": True, "files_count": 0}
            for p in targets:
                try:
                    cmd.append(p.relative_to(project_root).as_posix())
                except ValueError:
                    cmd.append(str(p))
            count = len(targets)
        else:
            cmd.append(".")
            count = 0

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
                "engine": "biome",
                "available": True,
                "success": res.returncode in (0, 1),
                "files_count": count,
                "output": res.stdout.strip(),
            }
        except (OSError, subprocess.SubprocessError) as err:
            logger.debug("Biome fix execution error: %s", err)
            return {"engine": "biome", "available": True, "success": False, "error": str(err)}
