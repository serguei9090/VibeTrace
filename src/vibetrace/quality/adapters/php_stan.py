"""Production PHP Adapter for Quality, Types & Cognitive Complexity Analysis.

Executes PHPStan (or vendor/bin/phpstan) in subprocess with native JSON error formatting,
maps PHPStan diagnostics and rule identifiers to canonical Sonar RSPEC IDs, and
orchestrates safe auto-fixing via Pint / PHP-CS-Fixer.
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

SUPPORTED_EXTENSIONS = frozenset({".php", ".phtml"})

PHPSTAN_TO_SONAR: dict[str, tuple[str, str, QualityCategory, QualitySeverity]] = {
    "cognitiveComplexity.tooHigh": (
        "S3776",
        "RSPEC-3776",
        QualityCategory.COMPLEXITY,
        QualitySeverity.HIGH,
    ),
    "cyclomaticComplexity.tooHigh": (
        "S1541",
        "RSPEC-1541",
        QualityCategory.COMPLEXITY,
        QualitySeverity.MEDIUM,
    ),
    "method.deprecated": (
        "S1874",
        "RSPEC-1874",
        QualityCategory.MAINTAINABILITY,
        QualitySeverity.MEDIUM,
    ),
    "function.deprecated": (
        "S1874",
        "RSPEC-1874",
        QualityCategory.MAINTAINABILITY,
        QualitySeverity.MEDIUM,
    ),
    "class.deprecated": (
        "S1874",
        "RSPEC-1874",
        QualityCategory.MAINTAINABILITY,
        QualitySeverity.MEDIUM,
    ),
    "variable.unused": (
        "S1481",
        "RSPEC-1481",
        QualityCategory.MAINTAINABILITY,
        QualitySeverity.LOW,
    ),
    "deadCode.unreachable": (
        "S1854",
        "RSPEC-1854",
        QualityCategory.MAINTAINABILITY,
        QualitySeverity.LOW,
    ),
    "method.notFound": (
        "S3785",
        "RSPEC-3785",
        QualityCategory.RELIABILITY,
        QualitySeverity.HIGH,
    ),
    "property.notFound": (
        "S3785",
        "RSPEC-3785",
        QualityCategory.RELIABILITY,
        QualitySeverity.HIGH,
    ),
    "class.notFound": (
        "S3785",
        "RSPEC-3785",
        QualityCategory.RELIABILITY,
        QualitySeverity.HIGH,
    ),
    "nullCoalesce.variable": (
        "S2259",
        "RSPEC-2259",
        QualityCategory.RELIABILITY,
        QualitySeverity.MEDIUM,
    ),
    "arguments.count": (
        "S107",
        "RSPEC-107",
        QualityCategory.MAINTAINABILITY,
        QualitySeverity.MEDIUM,
    ),
    "return.missing": (
        "S3626",
        "RSPEC-3626",
        QualityCategory.CODE_SMELL,
        QualitySeverity.LOW,
    ),
}


class PhpStanAdapter(LinterSubEngine):
    """Sub-engine adapter executing PHPStan and normalizing findings to Sonar RSPEC."""

    name = "phpstan"
    SUPPORTED_EXTENSIONS = SUPPORTED_EXTENSIONS

    def __init__(self, timeout_sec: float = 30.0) -> None:
        self.timeout_sec = timeout_sec
        self._cached_runner: list[str] | None = None
        self._cached_fixer: list[str] | None = None

    def _find_composer_json(self, project_root: Path) -> Path | None:
        """Locates composer.json at project root or subdirectories."""
        for cand in (project_root / "composer.json", project_root / "src" / "composer.json"):
            if cand.is_file():
                return cand
        return None

    def _resolve_config_path(self, project_root: Path) -> Path | None:
        """Resolves active phpstan.neon configuration."""
        from vibetrace.config import resolve_linter_mode

        mode = resolve_linter_mode(project_root, "php")
        if mode == "project":
            for cand in (
                "phpstan.neon",
                "phpstan.neon.dist",
                "phpstan.dist.neon",
            ):
                p = project_root / cand
                if p.is_file():
                    return p

        override = project_root / ".vibetrace" / "config" / "phpstan.neon"
        if override.is_file():
            return override

        for cand in ("phpstan.neon", "phpstan.neon.dist", "phpstan.dist.neon"):
            p = project_root / cand
            if p.is_file():
                return p
        return None

    def _find_runner(self, project_root: Path) -> list[str] | None:
        """Finds working PHPStan executable in workspace or system."""
        if self._cached_runner is not None:
            return self._cached_runner

        candidates: list[list[str]] = []
        is_win = os.name == "nt"
        bat_ext = ".bat" if is_win else ""

        # 1. Project local vendor/bin/phpstan
        vendor_bin = project_root / "vendor" / "bin" / f"phpstan{bat_ext}"
        if vendor_bin.is_file():
            candidates.append([str(vendor_bin)])

        # 2. System PATH
        which_phpstan = shutil.which("phpstan")
        if which_phpstan:
            candidates.append([which_phpstan])

        # 3. User cached ~/.vibetrace/bin/phpstan
        user_bin = Path.home() / ".vibetrace" / "bin" / f"phpstan{bat_ext}"
        if user_bin.is_file():
            candidates.append([str(user_bin)])

        # 4. Standalone phpstan.phar with PHP runtime
        which_php = shutil.which("php")
        if which_php:
            phar_paths = [
                project_root / ".vibetrace" / "tools" / "phpstan.phar",
                Path.home() / ".vibetrace" / "bin" / "phpstan.phar",
            ]
            for ph in phar_paths:
                if ph.is_file():
                    candidates.append([which_php, str(ph)])

        # 5. Composer exec phpstan
        which_composer = shutil.which("composer")
        if which_composer:
            candidates.append([which_composer, "exec", "phpstan", "--"])

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

    def _find_fixer(self, project_root: Path) -> list[str] | None:
        """Finds safe auto-fixer runner (Pint, PHP-CS-Fixer, phpcbf)."""
        if self._cached_fixer is not None:
            return self._cached_fixer

        is_win = os.name == "nt"
        bat_ext = ".bat" if is_win else ""
        fixer_candidates: list[list[str]] = []

        # 1. Laravel Pint
        pint_bin = project_root / "vendor" / "bin" / f"pint{bat_ext}"
        if pint_bin.is_file():
            fixer_candidates.append([str(pint_bin)])
        elif shutil.which("pint"):
            fixer_candidates.append([shutil.which("pint")])  # type: ignore

        # 2. PHP-CS-Fixer
        cs_fixer = project_root / "vendor" / "bin" / f"php-cs-fixer{bat_ext}"
        if cs_fixer.is_file():
            fixer_candidates.append([str(cs_fixer), "fix"])
        elif shutil.which("php-cs-fixer"):
            fixer_candidates.append([shutil.which("php-cs-fixer"), "fix"])  # type: ignore

        # 3. phpcbf
        phpcbf = project_root / "vendor" / "bin" / f"phpcbf{bat_ext}"
        if phpcbf.is_file():
            fixer_candidates.append([str(phpcbf)])

        for cand in fixer_candidates:
            self._cached_fixer = cand
            return cand
        return None

    def is_available(self, project_root: Path) -> bool:
        """Returns True if PHPStan runner is found and composer.json or .php files exist."""
        has_runner = self._find_runner(project_root) is not None
        if not has_runner:
            return False
        if self._find_composer_json(project_root) is not None:
            return True
        return any(project_root.rglob("*.php"))

    def _map_message_to_finding(
        self,
        msg_obj: dict[str, Any],
        raw_file: str,
        project_root: Path,
    ) -> QualityFinding | None:
        """Maps a PHPStan diagnostic message to a Sonar RSPEC QualityFinding."""
        file_path = (project_root / raw_file).resolve()
        try:
            rel_file = file_path.relative_to(project_root).as_posix()
        except ValueError:
            rel_file = str(raw_file).replace("\\", "/")

        line = int(msg_obj.get("line") or 1)
        text = str(msg_obj.get("message") or "PHPStan quality issue").strip()
        identifier = str(msg_obj.get("identifier") or "").strip()

        rule_info = PHPSTAN_TO_SONAR.get(identifier)
        if not rule_info:
            # Fuzzy match common message patterns if identifier is generic
            text_lower = text.lower()
            if "cognitive complexity" in text_lower:
                rule_info = (
                    "S3776",
                    "RSPEC-3776",
                    QualityCategory.COMPLEXITY,
                    QualitySeverity.HIGH,
                )
            elif "deprecated" in text_lower:
                rule_info = (
                    "S1874",
                    "RSPEC-1874",
                    QualityCategory.MAINTAINABILITY,
                    QualitySeverity.MEDIUM,
                )
            elif "unused" in text_lower:
                rule_info = (
                    "S1481",
                    "RSPEC-1481",
                    QualityCategory.MAINTAINABILITY,
                    QualitySeverity.LOW,
                )
            elif "undefined method" in text_lower or "call to an undefined" in text_lower:
                rule_info = (
                    "S3785",
                    "RSPEC-3785",
                    QualityCategory.RELIABILITY,
                    QualitySeverity.HIGH,
                )
            else:
                rule_info = (
                    "S1135",
                    "RSPEC-1135",
                    QualityCategory.CODE_SMELL,
                    QualitySeverity.LOW,
                )

        s_code, rspec_id, category, severity = rule_info
        sonar_rule_id = f"php:{s_code}"
        hash_id = hashlib.sha256(f"{rel_file}:{line}:{identifier}:{text}".encode()).hexdigest()[:12]

        finding = QualityFinding(
            id=f"QUAL-{s_code}-{hash_id}",
            rule_id=sonar_rule_id,
            rspec_id=rspec_id,
            category=category,
            severity=severity,
            title=f"PHP {identifier or 'quality'}: {text[:80]}",
            message=text,
            file_path=rel_file,
            start_line=line,
            end_line=line,
            code_hash=hash_id,
            remediation_guidance=(
                "Resolve PHPStan static analysis finding according to modern PHP standards."
            ),
        )

        spec = get_rule_spec(resolve_canonical_rule_id(sonar_rule_id))
        if spec:
            finding.title = spec.title
            if spec.explanation:
                finding.remediation_guidance = spec.explanation
            if spec.compliant_example:
                finding.compliant_snippet = spec.get_compliant_example("php")

        return finding

    def _parse_phpstan_output(
        self,
        output: str,
        project_root: Path,
    ) -> list[QualityFinding]:
        """Parses PHPStan JSON error format output into QualityFindings."""
        if not output.strip():
            return []

        try:
            parsed = json.loads(extract_json_payload(output))
        except Exception:
            return []

        findings: list[QualityFinding] = []
        files_obj = parsed.get("files") or {}
        if isinstance(files_obj, dict):
            for file_key, file_val in files_obj.items():
                messages = file_val.get("messages") or []
                for msg in messages:
                    f = self._map_message_to_finding(msg, file_key, project_root)
                    if f:
                        findings.append(f)

        return findings

    def _run_phpstan(self, project_root: Path, args: list[str]) -> str:
        """Executes PHPStan process in project root."""
        runner = self._find_runner(project_root)
        if not runner:
            return ""

        config_path = self._resolve_config_path(project_root)
        config_arg = ["-c", str(config_path)] if config_path else []
        cmd = [*runner, "analyse", "--error-format=json", "--no-progress", *config_arg, *args]

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
            return res.stdout or ""
        except (OSError, subprocess.SubprocessError) as err:
            logger.debug("Failed to execute PHPStan: %s", err)
            return ""

    def audit_file(self, file_path: Path, project_root: Path) -> list[QualityFinding]:
        """Audits a single target PHP file."""
        if file_path.suffix.lower() not in SUPPORTED_EXTENSIONS:
            return []
        return self.audit_project(project_root, [file_path])

    def audit_project(
        self,
        project_root: Path,
        file_list: list[Path] | None = None,
    ) -> list[QualityFinding]:
        """Audits target files or entire PHP project."""
        target_args: list[str] = []
        target_rel_paths: set[str] | None = None

        if file_list is not None:
            targets = [p for p in file_list if p.suffix.lower() in SUPPORTED_EXTENSIONS]
            if not targets:
                return []
            target_rel_paths = set()
            for p in targets:
                try:
                    rel = p.relative_to(project_root).as_posix()
                except ValueError:
                    rel = str(p).replace("\\", "/")
                target_rel_paths.add(rel)
                target_args.append(rel)
        else:
            target_args = ["."]

        output = self._run_phpstan(project_root, target_args)
        findings = self._parse_phpstan_output(output, project_root)

        if target_rel_paths is not None:
            findings = [f for f in findings if f.file_path in target_rel_paths]

        return findings

    def fix_files(
        self,
        project_root: Path,
        file_list: list[Path] | None = None,
    ) -> dict[str, Any]:
        """Applies safe code fixes using Pint, PHP-CS-Fixer, or phpcbf."""
        fixer = self._find_fixer(project_root)
        if not fixer:
            return {"engine": "php", "available": False, "success": False, "files_count": 0}

        targets: list[str] = []
        if file_list is not None:
            for p in file_list:
                if p.suffix.lower() in SUPPORTED_EXTENSIONS:
                    try:
                        targets.append(p.relative_to(project_root).as_posix())
                    except ValueError:
                        targets.append(str(p))
            if not targets:
                return {"engine": "php", "available": True, "success": True, "files_count": 0}
        else:
            targets = ["."]

        cmd = [*fixer, *targets]
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
                "engine": "php",
                "available": True,
                "success": res.returncode in (0, 1),
                "files_count": len(targets) if targets != ["."] else 0,
                "output": res.stdout.strip(),
            }
        except (OSError, subprocess.SubprocessError) as err:
            logger.debug("PHP fix execution error: %s", err)
            return {"engine": "php", "available": True, "success": False, "error": str(err)}
