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
            timeout=5,
            check=False,
        )
        if res.returncode == 0:
            sha = res.stdout.strip()
            return sha if len(sha) == 40 else None
    except (subprocess.SubprocessError, OSError):
        pass
    return None


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

    try:
        # 1. Compare base_sha against current HEAD and staged/unstaged changes
        diff_res = subprocess.run(
            ["git", "-C", str(repo_root), "diff", "--name-status", "-M", base_sha],
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
        )
        if diff_res.returncode != 0:
            return None

        # 2. Check for untracked files
        status_res = subprocess.run(
            ["git", "-C", str(repo_root), "status", "--porcelain", "-uall"],
            capture_output=True,
            text=True,
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
                    deleted.add(parts[1].replace("\\", "/"))
            elif status.startswith("R"):
                if len(parts) >= 3:
                    deleted.add(parts[1].replace("\\", "/"))
                    changed.add(parts[2].replace("\\", "/"))
            else:
                if len(parts) > 1:
                    changed.add(parts[1].replace("\\", "/"))

        for raw_line in status_res.stdout.splitlines():
            line = raw_line.rstrip("\r\n")
            if len(line) < 4:
                continue
            status = line[:2]
            path = line[3:].strip().replace("\\", "/")
            if not path:
                continue
            if "D" in status:
                deleted.add(path)
            else:
                changed.add(path)

        # Reconcile deleted vs changed
        changed -= deleted

        # Verify disk existence for added/changed files
        verified_changed: set[str] = set()
        for p in changed:
            if (repo_root / p).exists():
                verified_changed.add(p)
            else:
                deleted.add(p)

        return verified_changed, deleted

    except (subprocess.SubprocessError, OSError):
        return None
