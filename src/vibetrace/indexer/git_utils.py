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

        # 2. Check for untracked files
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

        for raw_line in status_res.stdout.splitlines():
            line = raw_line.rstrip("\r\n")
            if len(line) < 4:
                continue
            status = line[:2]
            raw_path = line[3:].strip()
            if not raw_path:
                continue

            # Handle staged or working-tree file renames or copies:
            # 'R  old -> new' or 'C  orig -> copy'
            if " -> " in raw_path:
                old_p, new_p = raw_path.split(" -> ", 1)
                clean_old = _decode_git_path(old_p)
                clean_new = _decode_git_path(new_p)
                if "R" in status:
                    deleted.add(clean_old)
                changed.add(clean_new)
            else:
                clean_p = _decode_git_path(raw_path)
                if "D" in status:
                    deleted.add(clean_p)
                else:
                    changed.add(clean_p)

        # Disk is the source of truth: a path deleted in history but present now
        # (re-created, `git rm --cached`, reverted delete) must be re-indexed, not purged.
        all_paths = changed | deleted
        verified_changed = {
            p for p in all_paths if (repo_root / p).exists() or (repo_root / p).is_symlink()
        }
        return verified_changed, all_paths - verified_changed

    except (subprocess.SubprocessError, OSError):
        return None
