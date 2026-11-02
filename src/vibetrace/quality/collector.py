"""File Collection & Inline Suppression Resolution for Quality Auditing.

Discovers supported polyglot source files, filters ignored directory segments,
and parses inline `# noqa`, `// nosonar`, and `vibetrace:ignore` directives.
"""

from __future__ import annotations

import re
from pathlib import Path

from vibetrace.quality.models import QualityFinding
from vibetrace.quality.rules import resolve_canonical_rule_id

SUPPORTED_EXTS = (
    ".py",
    ".css",
    ".ts",
    ".tsx",
    ".js",
    ".jsx",
    ".mjs",
    ".cjs",
    ".mts",
    ".cts",
    ".json",
    ".jsonc",
    ".go",
    ".rs",
    ".php",
    ".phtml",
    ".rb",
    ".sh",
    ".bash",
    ".yaml",
    ".yml",
    ".sql",
)

IGNORE_PATH_PARTS = (
    "/.venv/",
    "/venv/",
    "/__pycache__/",
    "/.git/",
    "/scratch/",
    "/node_modules/",
    "/dist/",
    "/build/",
    "/out/",
    "/.next/",
    "/target/",
)

_INLINE_NOSONAR_RE = re.compile(r"(?:#|//)\s*nosonar\b", re.IGNORECASE)
_INLINE_NOQA_RE = re.compile(r"(?:#|//)\s*noqa(?::\s*([\w-]+))?", re.IGNORECASE)
_INLINE_IGNORE_RE = re.compile(
    r"(?:#|//)\s*vibetrace:ignore(?:\[([\w:-]+)\])?(?::\s*(.+))?",
    re.IGNORECASE,
)
INLINE_IGNORE_RE = _INLINE_IGNORE_RE


def is_suppressed_by_line(finding: QualityFinding, line: str, rule_key: str) -> bool:
    """Evaluates whether a single line matches an inline suppression for this rule."""
    if "#" not in line and "//" not in line:
        return False

    if _INLINE_NOSONAR_RE.search(line):
        finding.status = "APPROVED"
        finding.tag = "inline-comment"
        finding.rationale = "Suppressed via nosonar"
        finding.reviewed_by = "inline-comment"
        return True

    m_noqa = _INLINE_NOQA_RE.search(line)
    if m_noqa:
        matched_rule = m_noqa.group(1)
        if matched_rule and resolve_canonical_rule_id(matched_rule) != rule_key:
            return False
        finding.status = "APPROVED"
        finding.tag = "inline-comment"
        finding.rationale = "Suppressed via noqa"
        finding.reviewed_by = "inline-comment"
        return True

    m_ignore = _INLINE_IGNORE_RE.search(line)
    if m_ignore:
        matched_rule = m_ignore.group(1)
        if matched_rule and resolve_canonical_rule_id(matched_rule) != rule_key:
            return False
        finding.status = "APPROVED"
        finding.tag = "inline-comment"
        finding.rationale = (m_ignore.group(2) or "Suppressed via inline comment").strip()
        finding.reviewed_by = "inline-comment"
        return True

    return False


def check_inline_suppression(finding: QualityFinding, source_lines: list[str]) -> bool:
    """Checks if finding has an inline suppression comment immediately above or on the line."""
    target_idx = finding.start_line - 1
    rule_key = resolve_canonical_rule_id(finding.rule_id)
    for idx in (target_idx - 2, target_idx - 1, target_idx):
        if 0 <= idx < len(source_lines) and is_suppressed_by_line(
            finding, source_lines[idx].strip(), rule_key
        ):
            return True
    return False


def resolve_candidate_paths(
    project_root: Path,
    file_filter: str | None = None,
    file_list: list[str] | None = None,
) -> list[Path]:
    """Resolves candidate source files across all supported extensions."""
    if file_list:
        return [
            p
            for p_str in file_list
            if (p := (project_root / p_str).resolve()).is_file()
            and (
                p_str.endswith(SUPPORTED_EXTS)
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
            for ext in SUPPORTED_EXTS:
                found.extend(target.rglob(f"*{ext}"))
            found.extend(target.rglob("Dockerfile*"))
            return found
        return []

    src_dir = project_root / "src"
    search_root = src_dir if src_dir.is_dir() else project_root
    found_all: list[Path] = []
    for ext in SUPPORTED_EXTS:
        found_all.extend(search_root.rglob(f"*{ext}"))
    found_all.extend(search_root.rglob("Dockerfile*"))
    return found_all


def is_path_allowed(path: Path, include_tests: bool) -> bool:
    """Checks whether a resolved path is allowed for quality auditing."""
    posix_path = path.as_posix()
    if any(seg in posix_path for seg in IGNORE_PATH_PARTS):
        return False
    if not include_tests:
        parts = posix_path.split("/")
        if any(p in ("tests", "test", "e2e", "__tests__") for p in parts[:-1]):
            return False
        fname = path.name.lower()
        if fname.startswith("test_") or fname.endswith(
            ("_test.py", "_test.go", ".spec.ts", ".test.ts", ".spec.tsx", ".test.tsx")
        ):
            return False
    return True


def collect_source_files(
    project_root: Path,
    file_filter: str | None = None,
    file_list: list[str] | None = None,
    include_tests: bool = False,
) -> list[Path]:
    """Resolves and filters all target files for quality auditing."""
    candidates = resolve_candidate_paths(project_root, file_filter, file_list)
    return [p for p in candidates if is_path_allowed(p, include_tests)]
