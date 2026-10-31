"""Dockerfile Quality and Maintainability Analyzer.

Evaluates Dockerfiles for maintainability and predictable build hygiene:
- docker:S6505 / RSPEC-6505: Use 'COPY' instead of 'ADD' for local files
- docker:S6504 / RSPEC-6504: Duplicate CMD or ENTRYPOINT in the same build stage
- docker:S6506 / RSPEC-6506: Relative WORKDIR path (should be absolute)

Note: Security boundaries (such as running as root CWE-250 and secrets in ENV)
are exclusively audited in `vibetrace.security.iac_scanner` to prevent duplication.
"""

from __future__ import annotations

import hashlib
import re

from vibetrace.quality.models import (
    QualityCategory,
    QualityFinding,
    QualitySeverity,
)
from vibetrace.quality.rules import QUALITY_RULES_CATALOG

_REMOTE_OR_ARCHIVE_RE = re.compile(
    r"(?:https?://|\.tar(?:\.gz|\.bz2|\.xz)?$|\.tgz$)", re.IGNORECASE
)


class DockerQualityAnalyzer:
    """Audits Dockerfile instructions for maintainability and build predictability."""

    def __init__(self, file_path: str, source_code: str) -> None:
        self.file_path = file_path.replace("\\", "/")
        self.source_code = source_code
        self.source_lines = source_code.splitlines()
        self.findings: list[QualityFinding] = []

    def _hash_snippet(self, snippet: str) -> str:
        return hashlib.sha256(snippet.strip().encode("utf-8")).hexdigest()

    def _make_finding_id(self, rule_id: str, line_no: int, desc: str) -> str:
        raw = f"{self.file_path}:{line_no}:{desc}"
        return f"QUAL-{rule_id.split(':')[-1]}-{hashlib.sha256(raw.encode()).hexdigest()[:12]}"

    def _check_add_instruction(self, line_no: int, line_text: str, args: str) -> None:
        """docker:S6505: Use 'COPY' instead of 'ADD' for local files."""
        tokens = args.split()
        if not tokens:
            return

        src = tokens[0]
        # ADD is permissible only if downloading remote URL or unpacking archive
        if not _REMOTE_OR_ARCHIVE_RE.search(src):
            fid = self._make_finding_id("S6505", line_no, "use_copy")
            spec = QUALITY_RULES_CATALOG.get("S6505")
            self.findings.append(
                QualityFinding(
                    id=fid,
                    rule_id="docker:S6505",
                    rspec_id="RSPEC-6505",
                    category=QualityCategory.MAINTAINABILITY,
                    severity=QualitySeverity.LOW,
                    title="Use 'COPY' instead of 'ADD' for local files and directories.",
                    message=(
                        f"Instruction 'ADD {args}' copies local files. Use 'COPY' instead of 'ADD' "
                        "for predictable and transparent file copying."
                    ),
                    file_path=self.file_path,
                    start_line=line_no,
                    end_line=line_no,
                    symbol_name="ADD",
                    code_hash=self._hash_snippet(line_text),
                    non_compliant_snippet=line_text.strip(),
                    compliant_snippet=spec.compliant_example if spec else None,
                    remediation_guidance=spec.explanation if spec else None,
                )
            )

    def _check_workdir_instruction(self, line_no: int, line_text: str, args: str) -> None:
        """docker:S6506: 'WORKDIR' should use an absolute path instead of a relative path."""
        target_path = args.strip()
        # Absolute paths start with / or $ (env var) or C:\ on Windows
        is_absolute = target_path.startswith(("/", "$", "\\")) or (
            len(target_path) > 2 and target_path[1] == ":"
        )
        if not is_absolute:
            fid = self._make_finding_id("S6506", line_no, "relative_workdir")
            spec = QUALITY_RULES_CATALOG.get("S6506")
            self.findings.append(
                QualityFinding(
                    id=fid,
                    rule_id="docker:S6506",
                    rspec_id="RSPEC-6506",
                    category=QualityCategory.MAINTAINABILITY,
                    severity=QualitySeverity.MEDIUM,
                    title="'WORKDIR' should use an absolute path instead of a relative path.",
                    message=(
                        f"WORKDIR path '{target_path}' is relative. Use an absolute path "
                        f"(e.g. '/{target_path}') so the working directory does not depend "
                        "on base image defaults."
                    ),
                    file_path=self.file_path,
                    start_line=line_no,
                    end_line=line_no,
                    symbol_name="WORKDIR",
                    code_hash=self._hash_snippet(line_text),
                    non_compliant_snippet=line_text.strip(),
                    compliant_snippet=spec.compliant_example if spec else None,
                    remediation_guidance=spec.explanation if spec else None,
                )
            )

    def _evaluate_duplicate_commands(
        self, cmd_locations: list[tuple[int, str]], entrypoint_locations: list[tuple[int, str]]
    ) -> None:
        """docker:S6504: Flags duplicate CMD or ENTRYPOINT in the same stage."""
        spec = QUALITY_RULES_CATALOG.get("S6504")

        if len(cmd_locations) > 1:
            # All except the last CMD are dead/overridden code
            for line_no, line_text in cmd_locations[:-1]:
                fid = self._make_finding_id("S6504", line_no, "duplicate_cmd")
                self.findings.append(
                    QualityFinding(
                        id=fid,
                        rule_id="docker:S6504",
                        rspec_id="RSPEC-6504",
                        category=QualityCategory.RELIABILITY,
                        severity=QualitySeverity.HIGH,
                        title="Multiple 'CMD' instructions defined in the same build stage.",
                        message=(
                            "Only the last 'CMD' instruction takes effect. This earlier 'CMD' "
                            "is dead configuration and will be ignored by Docker."
                        ),
                        file_path=self.file_path,
                        start_line=line_no,
                        end_line=line_no,
                        symbol_name="CMD",
                        code_hash=self._hash_snippet(line_text),
                        non_compliant_snippet=line_text.strip(),
                        compliant_snippet=spec.compliant_example if spec else None,
                        remediation_guidance=spec.explanation if spec else None,
                    )
                )

        if len(entrypoint_locations) > 1:
            for line_no, line_text in entrypoint_locations[:-1]:
                fid = self._make_finding_id("S6504", line_no, "duplicate_entrypoint")
                self.findings.append(
                    QualityFinding(
                        id=fid,
                        rule_id="docker:S6504",
                        rspec_id="RSPEC-6504",
                        category=QualityCategory.RELIABILITY,
                        severity=QualitySeverity.HIGH,
                        title="Multiple 'ENTRYPOINT' instructions defined in the same build stage.",
                        message=(
                            "Only the last 'ENTRYPOINT' instruction takes effect. This earlier "
                            "instruction is dead configuration and will be ignored by Docker."
                        ),
                        file_path=self.file_path,
                        start_line=line_no,
                        end_line=line_no,
                        symbol_name="ENTRYPOINT",
                        code_hash=self._hash_snippet(line_text),
                        non_compliant_snippet=line_text.strip(),
                        compliant_snippet=spec.compliant_example if spec else None,
                        remediation_guidance=spec.explanation if spec else None,
                    )
                )

    def analyze(self) -> list[QualityFinding]:
        """Scans Dockerfile lines across multi-stage build definitions."""
        stage_cmds: list[tuple[int, str]] = []
        stage_entrypoints: list[tuple[int, str]] = []

        for line_no, raw_line in enumerate(self.source_lines, start=1):
            line = raw_line.strip()
            if not line or line.startswith("#"):
                continue

            parts = line.split(maxsplit=1)
            inst = parts[0].upper()
            args = parts[1] if len(parts) > 1 else ""

            if inst == "FROM":
                # New stage begins; check duplicates from previous stage
                self._evaluate_duplicate_commands(stage_cmds, stage_entrypoints)
                stage_cmds.clear()
                stage_entrypoints.clear()
            elif inst == "CMD":
                stage_cmds.append((line_no, raw_line))
            elif inst == "ENTRYPOINT":
                stage_entrypoints.append((line_no, raw_line))
            elif inst == "ADD":
                self._check_add_instruction(line_no, raw_line, args)
            elif inst == "WORKDIR":
                self._check_workdir_instruction(line_no, raw_line, args)

        # Flush final stage
        self._evaluate_duplicate_commands(stage_cmds, stage_entrypoints)
        return self.findings


def analyze_docker_quality(file_path: str, source_code: str) -> list[QualityFinding]:
    """Analyzes a Dockerfile for maintainability and build predictability smells."""
    analyzer = DockerQualityAnalyzer(file_path=file_path, source_code=source_code)
    return analyzer.analyze()
