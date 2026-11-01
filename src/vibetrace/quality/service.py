"""Quality & Maintainability Audit Service coordinating complexity and smells analyzers."""

from __future__ import annotations

import re
from collections import defaultdict
from pathlib import Path
from typing import Any

from vibetrace.indexer.db import IndexerDB
from vibetrace.quality.baseline import QualityBaselineManager
from vibetrace.quality.complexity import (
    FunctionComplexity,
    analyze_python_complexities,
)
from vibetrace.quality.css_analyzer import analyze_css_quality
from vibetrace.quality.declarative_rules import (
    DeclarativeQualityRule,
    DeclarativeQualityRuleManager,
)
from vibetrace.quality.docker_analyzer import analyze_docker_quality
from vibetrace.quality.models import (
    QualityAuditReport,
    QualityExceptionRecord,
    QualityFinding,
)
from vibetrace.quality.rules import get_rule_spec, resolve_canonical_rule_id
from vibetrace.quality.smells import analyze_python_smells
from vibetrace.quality.ts_analyzer import analyze_ts_quality
from vibetrace.taxonomy import CweTaxonomyManager

_SUPPORTED_EXTS = (".py", ".css", ".ts", ".tsx", ".js", ".jsx")
_IGNORE_PATH_PARTS = ("/.venv/", "/venv/", "/__pycache__/", "/.git/", "/scratch/", "/node_modules/")
_INLINE_IGNORE_RE = re.compile(
    r"(?:#|//)\s*(?:nosonar|noqa(?::\s*([a-z0-9_-]+))?|vibetrace:ignore\b(?:\s*\[([a-z0-9_:-]+)\])?(?:\s*:\s*(.+))?)",
    re.IGNORECASE,
)


def _is_suppressed_by_line(finding: QualityFinding, line: str, rule_key: str) -> bool:
    """Evaluates whether a single line matches an inline suppression for this rule."""
    m = _INLINE_IGNORE_RE.search(line)
    if not m:
        return False
    matched_rule = m.group(1) or m.group(2)
    if matched_rule and resolve_canonical_rule_id(matched_rule) != rule_key:
        return False
    finding.status = "APPROVED"
    finding.tag = "inline-comment"
    finding.rationale = (m.group(3) or "Suppressed via inline comment").strip()
    finding.reviewed_by = "inline-comment"
    return True


def _check_inline_suppression(finding: QualityFinding, source_lines: list[str]) -> bool:
    """Checks if finding has an inline suppression comment immediately above or on the line."""
    target_idx = finding.start_line - 1
    rule_key = resolve_canonical_rule_id(finding.rule_id)
    for idx in (target_idx - 2, target_idx - 1, target_idx):
        if 0 <= idx < len(source_lines) and _is_suppressed_by_line(
            finding, source_lines[idx].strip(), rule_key
        ):
            return True
    return False


def _resolve_candidate_paths(
    project_root: Path,
    file_filter: str | None = None,
    file_list: list[str] | None = None,
) -> list[Path]:
    """Resolves candidate source files (.py, .css, .ts, .tsx, Dockerfile)."""
    if file_list:
        return [
            p
            for p_str in file_list
            if (p := (project_root / p_str).resolve()).is_file()
            and (
                p_str.endswith(_SUPPORTED_EXTS)
                or p.name.lower() == "dockerfile"
                or p.name.lower().startswith("dockerfile.")
            )
        ]
    if file_filter:
        target = (project_root / file_filter).resolve()
        if target.is_file():
            return [target]
        if target.is_dir():
            found: list[Path] = []
            for ext in _SUPPORTED_EXTS:
                found.extend(target.rglob(f"*{ext}"))
            found.extend(target.rglob("Dockerfile*"))
            return found
        return []

    src_dir = project_root / "src"
    search_root = src_dir if src_dir.is_dir() else project_root
    found_all: list[Path] = []
    for ext in _SUPPORTED_EXTS:
        found_all.extend(search_root.rglob(f"*{ext}"))
    found_all.extend(search_root.rglob("Dockerfile*"))
    return found_all


def _is_path_allowed(path: Path, include_tests: bool) -> bool:
    """Checks whether a resolved path is allowed for quality auditing."""
    posix_path = path.as_posix()
    if any(seg in posix_path for seg in _IGNORE_PATH_PARTS):
        return False
    if not include_tests and ("/tests/" in posix_path or path.name.startswith("test_")):
        return False
    return True


def _collect_source_files(
    project_root: Path,
    file_filter: str | None = None,
    file_list: list[str] | None = None,
    include_tests: bool = False,
) -> list[Path]:
    """Resolves and filters all target files for quality auditing."""
    candidates = _resolve_candidate_paths(project_root, file_filter, file_list)
    return [p for p in candidates if _is_path_allowed(p, include_tests)]


def _audit_single_file(
    file_path: Path,
    project_root: Path,
    complexity_threshold: int,
    decl_rules: list[DeclarativeQualityRule] | None = None,
) -> tuple[list[FunctionComplexity], list[QualityFinding]]:
    """Analyzes a single source file (.py, .css, .ts, .tsx, Dockerfile) for quality smells."""
    try:
        rel_path = file_path.relative_to(project_root).as_posix()
    except ValueError:
        rel_path = file_path.as_posix()

    try:
        source_code = file_path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return [], []

    source_lines = source_code.splitlines()
    lower_name = file_path.name.lower()
    lower_suffix = file_path.suffix.lower()

    comps: list[FunctionComplexity] = []
    findings: list[QualityFinding] = []

    if lower_suffix == ".css":
        findings = analyze_css_quality(file_path=rel_path, source_code=source_code)
    elif lower_suffix in (".ts", ".tsx", ".js", ".jsx"):
        findings = analyze_ts_quality(file_path=rel_path, source_code=source_code)
    elif lower_name == "dockerfile" or lower_name.startswith("dockerfile."):
        findings = analyze_docker_quality(file_path=rel_path, source_code=source_code)
    else:
        file_comps, comp_findings = analyze_python_complexities(
            file_path=rel_path,
            source_code=source_code,
            threshold=complexity_threshold,
        )
        comps = file_comps
        findings.extend(comp_findings)

        smell_findings = analyze_python_smells(
            file_path=rel_path,
            source_code=source_code,
        )
        findings.extend(smell_findings)

    if decl_rules:
        decl_findings = DeclarativeQualityRuleManager.scan_file_with_rules(
            rel_path, project_root, decl_rules
        )
        findings.extend(decl_findings)

    for f in findings:
        _check_inline_suppression(f, source_lines)

    return comps, findings


class QualityService:
    """High-level service coordinating code quality and cognitive complexity audits."""

    @classmethod
    def audit_project(
        cls,
        db: IndexerDB | None,
        project_root: Path,
        file_filter: str | None = None,
        file_list: list[str] | None = None,
        complexity_threshold: int = 15,
        include_tests: bool = False,
    ) -> QualityAuditReport:
        """Audits project source files for cognitive complexity and clean code smells."""
        raw_findings: list[QualityFinding] = []
        all_complexities: list[FunctionComplexity] = []

        target_files = _collect_source_files(
            project_root=project_root,
            file_filter=file_filter,
            file_list=file_list,
            include_tests=include_tests,
        )

        decl_rules = DeclarativeQualityRuleManager.get_rules(db=db)

        for file_path in target_files:
            file_comps, file_findings = _audit_single_file(
                file_path=file_path,
                project_root=project_root,
                complexity_threshold=complexity_threshold,
                decl_rules=decl_rules,
            )
            all_complexities.extend(file_comps)
            raw_findings.extend(file_findings)

        for f in raw_findings:
            spec = get_rule_spec(f.rule_id)
            if spec:
                if spec.cwe_ids and not f.cwe_ids:
                    f.cwe_ids = list(spec.cwe_ids)
                    f.cwe_id = spec.cwe_ids[0]
                if spec.owasp and not f.owasp_category:
                    f.owasp_category = spec.owasp
            CweTaxonomyManager.enrich_finding(f)

        report = QualityBaselineManager.apply_baseline_to_findings(
            findings=raw_findings,
            project_root=project_root,
        )

        if all_complexities:
            report.average_complexity = sum(c.cognitive_complexity for c in all_complexities) / len(
                all_complexities
            )
            max_comp = max(all_complexities, key=lambda c: c.cognitive_complexity)
            report.max_complexity = max_comp.cognitive_complexity
            report.max_complexity_symbol = max_comp.name

        return report

    @classmethod
    def apply_auto_comments(
        cls,
        project_root: Path,
        findings: list[QualityFinding],
        rationale: str = "Vibecoding approved",
    ) -> int:
        """Injects inline suppression comments above flagged AST nodes."""
        by_file: dict[str, list[QualityFinding]] = defaultdict(list)
        for f in findings:
            if f.status in ("UNAPPROVED", "MODIFIED_CODE"):
                by_file[f.file_path].append(f)

        commented_count = 0
        for rel_path, file_findings in by_file.items():
            abs_path = (project_root / rel_path).resolve()
            if not abs_path.is_file():
                continue
            lines = abs_path.read_text(encoding="utf-8").splitlines()
            sorted_f = sorted(file_findings, key=lambda x: x.start_line, reverse=True)
            for f in sorted_f:
                idx = max(0, f.start_line - 1)
                indent = ""
                if idx < len(lines):
                    line_text = lines[idx]
                    indent = line_text[: len(line_text) - len(line_text.lstrip())]
                rule_short = f.rule_id.split(":")[-1]
                comment_line = f"{indent}# vibetrace:ignore[{rule_short}]: {rationale}  # NOSONAR"
                lines.insert(idx, comment_line)
                commented_count += 1
            abs_path.write_text("\n".join(lines) + "\n", encoding="utf-8")

        return commented_count

    @classmethod
    def record_exception(
        cls,
        project_root: Path,
        record: QualityExceptionRecord | None = None,
        **kwargs: Any,
    ) -> None:
        """Records an approved quality exception in .vibetrace/quality.toml."""
        QualityBaselineManager.record_exception(
            project_root=project_root,
            record=record,
            **kwargs,
        )
