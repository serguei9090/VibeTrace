"""Markdown glob pattern expansion for documentation catalog entries."""

from __future__ import annotations

import fnmatch
import os
import re
from pathlib import Path

from vibetrace.constants import IGNORED_DIRS_CASEFOLDED

MAGIC_CHARS = ("*", "?", "[", "]")
MARKDOWN_EXTS = frozenset({".md", ".markdown"})
MAX_PATTERN_MATCHES = 1000  # warn when a single pattern selects more files than this
_DRIVE_RE = re.compile(r"^[A-Za-z]:$")


def is_pattern(entry: str) -> bool:
    """True when a catalog entry contains glob magic characters."""
    return any(c in entry for c in MAGIC_CHARS)


def match_segments(pattern_segments: list[str], path_segments: list[str]) -> bool:
    """Case-insensitive segment match; a `**` segment matches zero or more segments."""
    if not pattern_segments:
        return not path_segments
    head = pattern_segments[0]
    if head == "**":
        return any(
            match_segments(pattern_segments[1:], path_segments[i:])
            for i in range(len(path_segments) + 1)
        )
    if not path_segments:
        return False
    if not fnmatch.fnmatchcase(path_segments[0].casefold(), head.casefold()):
        return False
    return match_segments(pattern_segments[1:], path_segments[1:])


def split_pattern(entry: str, base_dir: Path) -> tuple[Path, list[str]] | None:
    """Split a pattern into its base directory and glob segments.

    The base is the leading run of literal segments, resolved against
    ``base_dir`` (or used as absolute when the entry is absolute). Returns
    None for invalid patterns: empty, or containing a ``..`` segment.
    """
    norm = entry.replace("\\", "/")
    while norm.startswith("./"):
        norm = norm[2:]
    segments = [s for s in norm.split("/") if s]
    if not segments or any(s == ".." for s in segments):
        return None
    base_segs: list[str] = []
    for s in segments:
        if is_pattern(s):
            break
        base_segs.append(s)
    glob_segs = segments[len(base_segs):]
    base_str = "/".join(base_segs)
    if norm.startswith("/") or _DRIVE_RE.match(segments[0]) or Path(norm).is_absolute():
        base = Path(base_str) if base_str else Path("/")
    else:
        base = base_dir / base_str if base_str else base_dir
    return base, glob_segs


def expand_pattern(entry: str, base_dir: Path) -> tuple[list[Path], list[str]]:
    """Expand one catalog pattern entry into matched Markdown files.

    Returns (files, warnings). The walk prunes IGNORED_DIRS, never follows
    symlinks, keeps only .md/.markdown, and matches case-insensitively. Files
    are sorted by case-folded relative path.
    """
    warnings: list[str] = []
    split = split_pattern(entry, base_dir)
    if split is None:
        msg = f"catalog entry '{entry}' is not a valid pattern (empty or '..' segment); skipped"
        return [], [msg]
    base, glob_segs = split
    if not glob_segs:
        return [], [f"catalog entry '{entry}' has no glob part; skipped"]
    if not base.is_dir():
        msg = f"catalog entry '{entry}': base directory '{base}' does not exist; matched 0 files"
        return [], [msg]

    matches: list[Path] = []
    for root, dirs, files in os.walk(base, followlinks=False):
        dirs[:] = [d for d in dirs if d.casefold() not in IGNORED_DIRS_CASEFOLDED]
        for name in files:
            if Path(name).suffix.casefold() not in MARKDOWN_EXTS:
                continue
            p = Path(root) / name
            if match_segments(glob_segs, p.relative_to(base).parts):
                matches.append(p)
    matches.sort(key=lambda p: str(p.relative_to(base)).replace("\\", "/").casefold())
    if not matches:
        warnings.append(f"catalog entry '{entry}' matched 0 Markdown files under '{base}'")
    elif len(matches) > MAX_PATTERN_MATCHES:
        warnings.append(
            f"catalog entry '{entry}' matched {len(matches)} files (> {MAX_PATTERN_MATCHES})"
        )
    return matches, warnings


def pattern_matches(entry: str, base_dir: Path, path: Path) -> bool:
    """True when a concrete file ``path`` is selected by catalog pattern ``entry``.

    Used for live-sync (watcher) where no full expansion is wanted: checks
    Markdown extension, IGNORED_DIRS segments, and case-insensitive glob match
    against the pattern's base directory.
    """
    split = split_pattern(entry, base_dir)
    if split is None:
        return False
    base, glob_segs = split
    if not glob_segs or path.suffix.casefold() not in MARKDOWN_EXTS:
        return False
    try:
        rel = path.relative_to(base)
    except ValueError:
        return False
    parts = rel.parts
    if any(seg.casefold() in IGNORED_DIRS_CASEFOLDED for seg in parts[:-1]):
        return False
    return match_segments(glob_segs, parts)
