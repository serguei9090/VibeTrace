"""Git repository utilities for Git-SHA incremental delta caching."""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path


def get_git_sha(repo_root: Path) -> str | None:
    """Return the current Git commit SHA (HEAD) if inside a git repository, else None."""
    if not shutil.which("git"):
        return None
    try:
        res = subprocess.run(
            ["git", "-C", str(repo_root), "rev-parse", "HEAD"],
            capture_output=True,
            text=True,
            encoding="utf-8",
            timeout=5,
            check=False,
        )
        if res.returncode == 0:
            sha = res.stdout.strip()
            return (
                sha
                if len(sha) in (40, 64) and all(c in "0123456789abcdefABCDEF" for c in sha)
                else None
            )
    except (subprocess.SubprocessError, OSError):
        pass
    return None


def _decode_git_path(raw_path: str) -> str:
    """Safely decode Git C-style quoted paths without corrupting literal separators."""
    p = raw_path.strip()
    if p.startswith('"') and p.endswith('"') and len(p) >= 2:
        try:
            # Git escapes double quotes and backslashes in octal/C-style
            p = p[1:-1].encode("latin1").decode("unicode_escape")
        except Exception:
            p = p[1:-1]
    return p.replace("\\", "/")


def _parse_git_status_porcelain(stdout: str) -> tuple[set[str], set[str]]:
    """Parse `git status --porcelain` lines into `(changed_or_untracked, deleted)` sets."""
    changed: set[str] = set()
    deleted: set[str] = set()
    for raw_line in stdout.splitlines():
        line = raw_line.rstrip("\r\n")
        if len(line) < 4:
            continue
        status = line[:2]
        raw_path = line[3:].strip()
        if not raw_path:
            continue
        if " -> " in raw_path:
            old_p, new_p = raw_path.split(" -> ", 1)
            if "R" in status:
                deleted.add(_decode_git_path(old_p))
            changed.add(_decode_git_path(new_p))
        else:
            clean_p = _decode_git_path(raw_path)
            if "D" in status:
                deleted.add(clean_p)
            else:
                changed.add(clean_p)
    return changed, deleted


def get_git_changed_and_untracked_files(repo_root: Path) -> set[str] | None:
    """Return existing modified/untracked Git file paths, or None if not in Git."""
    if not shutil.which("git"):
        return None
    try:
        res = subprocess.run(
            [
                "git",
                "-c",
                "core.quotepath=false",
                "-C",
                str(repo_root),
                "status",
                "--porcelain",
                "-uall",
                "--",
                ".",
                ":(exclude).vibetrace",
                ":(exclude)vibetrace_context",
            ],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=10,
            check=False,
        )
        if res.returncode != 0:
            return None
    except (subprocess.SubprocessError, OSError):
        return None

    changed, _deleted = _parse_git_status_porcelain(res.stdout)
    filtered: set[str] = set()
    for p in changed:
        clean = p.lstrip("./")
        if (
            not clean
            or clean in (".vibetrace", "vibetrace_context")
            or clean.startswith((".vibetrace/", "vibetrace_context/"))
        ):
            continue
        if (repo_root / clean).exists() or (repo_root / clean).is_symlink():
            filtered.add(clean)
    return filtered


def filter_paths_to_git_changed(repo_root: Path, candidates: list[Path]) -> list[Path] | None:
    """Filter Paths to existing modified/untracked Git files, or None outside Git."""
    changed = get_git_changed_and_untracked_files(repo_root)
    if changed is None:
        return None
    if not changed:
        return []
    proj_res = repo_root.resolve()
    matched: list[Path] = []
    for cand in candidates:
        abs_p = cand if cand.is_absolute() else (proj_res / cand)
        try:
            rel = abs_p.resolve().relative_to(proj_res).as_posix().lstrip("./")
        except ValueError:
            rel = str(cand).replace("\\", "/").lstrip("./")
        if rel in changed:
            matched.append(cand)
    return matched


def resolve_git_audit_scope(
    project_root: Path,
    file_filter: str | None,
    file_list: list[str] | None,
    scan_all: bool,
) -> tuple[str, str | None, list[str] | None, bool]:
    """Resolve audit scope into `(scope_mode, scope_note, effective_file_list, empty_fast_exit)`."""
    if file_filter or file_list is not None:
        return "file", None, file_list, False
    if scan_all:
        return "all", None, None, False
    changed = get_git_changed_and_untracked_files(project_root)
    if changed is None:
        return "all", "Not a Git repository; scanned whole repository.", None, False
    if not changed:
        return (
            "changed",
            "No changed or untracked Git files to scan (pass --all for whole repository).",
            [],
            True,
        )
    return "changed", None, sorted(changed), False


def get_git_delta(
    repo_root: Path,
    base_sha: str | None = None,
) -> tuple[set[str], set[str]] | None:
    """
    Compute incremental file changes between a base Git SHA and the current working directory.

    Returns:
        tuple[set[str], set[str]]: (changed_or_added_paths, deleted_paths) relative to repo_root.
        None: If git is unavailable, repo_root is not a git repo, or base_sha is invalid.
    """
    if not base_sha or not shutil.which("git"):
        return None

    # Validate that base_sha looks like a valid git commit SHA to avoid option injection
    clean_sha = base_sha.strip()
    if not (len(clean_sha) in (40, 64) and all(c in "0123456789abcdefABCDEF" for c in clean_sha)):
        return None

    try:
        # 1. Compare base_sha against current HEAD and staged/unstaged changes
        diff_res = subprocess.run(
            [
                "git",
                "-c",
                "core.quotepath=false",
                "-C",
                str(repo_root),
                "diff",
                "--name-status",
                "-M",
                clean_sha,
                "--",
            ],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=10,
            check=False,
        )
        if diff_res.returncode != 0:
            return None

        # 2. Check for untracked files (excluding internal VibeTrace metadata dirs)
        status_res = subprocess.run(
            [
                "git",
                "-c",
                "core.quotepath=false",
                "-C",
                str(repo_root),
                "status",
                "--porcelain",
                "-uall",
                "--",
                ".",
                ":(exclude).vibetrace",
                ":(exclude)vibetrace_context",
            ],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=10,
            check=False,
        )
        if status_res.returncode != 0:
            return None

        changed: set[str] = set()
        deleted: set[str] = set()

        for line in diff_res.stdout.splitlines():
            line = line.strip()
            if not line:
                continue
            parts = line.split("\t")
            status = parts[0]
            if status.startswith("D"):
                if len(parts) > 1:
                    deleted.add(_decode_git_path(parts[1]))
            elif status.startswith("R"):
                if len(parts) >= 3:
                    deleted.add(_decode_git_path(parts[1]))
                    changed.add(_decode_git_path(parts[2]))
            elif status.startswith("C"):
                if len(parts) >= 3:
                    changed.add(_decode_git_path(parts[2]))
                elif len(parts) > 1:
                    changed.add(_decode_git_path(parts[1]))
            else:
                if len(parts) > 1:
                    changed.add(_decode_git_path(parts[1]))

        status_changed, status_deleted = _parse_git_status_porcelain(status_res.stdout)
        changed |= status_changed
        deleted |= status_deleted

        # Disk is the source of truth: a path deleted in history but present now
        # (re-created, `git rm --cached`, reverted delete) must be re-indexed, not purged.
        all_paths = {
            p
            for p in (changed | deleted)
            if p not in (".vibetrace", "vibetrace_context")
            and not p.startswith((".vibetrace/", "vibetrace_context/"))
        }
        verified_changed = {
            p for p in all_paths if (repo_root / p).exists() or (repo_root / p).is_symlink()
        }
        return verified_changed, all_paths - verified_changed

    except (subprocess.SubprocessError, OSError):
        return None
