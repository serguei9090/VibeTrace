"""Automated Git hook installer and manager for VibeTrace.

Supports both Lefthook (lefthook.yml) and native Git hooks (.git/hooks/post-commit).
"""

from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path
from typing import Any

HOOK_SCRIPT_BLOCK = """# --- VibeTrace Automated Indexer ---
if command -v uv >/dev/null 2>&1; then
    uv run vibetrace index
    uv run vibetrace docs index
elif command -v vibetrace >/dev/null 2>&1; then
    vibetrace index
    vibetrace docs index
fi
# --- End VibeTrace ---
"""


def _get_git_hooks_dir(root: Path) -> Path | None:
    """Resolve the git hooks directory, supporting regular repos, worktrees, and submodules."""
    git_entry = root / ".git"
    if not git_entry.exists():
        return None
    if git_entry.is_dir():
        return git_entry / "hooks"
    # In worktrees/submodules, .git is a file containing 'gitdir: <path>'
    try:
        content = git_entry.read_text(encoding="utf-8").strip()
        if content.startswith("gitdir:"):
            git_path = Path(content.split(":", 1)[1].strip())
            if not git_path.is_absolute():
                git_path = (root / git_path).resolve()
            common_dir_file = git_path / "commondir"
            if common_dir_file.exists():
                common_dir = Path(common_dir_file.read_text(encoding="utf-8").strip())
                if not common_dir.is_absolute():
                    common_dir = (git_path / common_dir).resolve()
                return common_dir / "hooks"
            return git_path / "hooks"
    except OSError:
        pass
    return None


def detect_hook_provider(project_root: Path) -> str:
    """Detect whether the repository uses Lefthook or standard Git hooks."""
    if (project_root / "lefthook.yml").exists() or (project_root / ".lefthook.yml").exists():
        return "lefthook"
    if (project_root / ".git").exists():
        return "git"
    return "none"


def get_hooks_status(project_root: Path) -> dict[str, Any]:
    """Inspect current hook installation status."""
    root = project_root.resolve()
    lh_file = root / "lefthook.yml" if (root / "lefthook.yml").exists() else root / ".lefthook.yml"
    lh_installed = False
    if lh_file.exists():
        try:
            content = lh_file.read_text(encoding="utf-8")
            lh_installed = "vibetrace-sync:" in content or "vibetrace index" in content
        except OSError:
            pass

    git_hooks_dir = _get_git_hooks_dir(root)
    git_pc_installed = False
    git_pm_installed = False

    if git_hooks_dir and git_hooks_dir.exists():
        post_commit_file = git_hooks_dir / "post-commit"
        post_merge_file = git_hooks_dir / "post-merge"

        if post_commit_file.exists():
            try:
                git_pc_installed = "vibetrace" in post_commit_file.read_text(encoding="utf-8")
            except OSError:
                pass

        if post_merge_file.exists():
            try:
                git_pm_installed = "vibetrace" in post_merge_file.read_text(encoding="utf-8")
            except OSError:
                pass

    return {
        "provider": detect_hook_provider(root),
        "lefthook": {
            "available": lh_file.exists(),
            "configured": lh_installed,
            "path": str(lh_file) if lh_file.exists() else None,
        },
        "git": {
            "post_commit": git_pc_installed,
            "post_merge": git_pm_installed,
            "hooks_dir": str(git_hooks_dir) if (git_hooks_dir and git_hooks_dir.exists()) else None,
        },
    }


def _inject_lefthook_hook(content: str, hook_name: str) -> str:
    """Inject vibetrace-sync into an existing or new hook section without duplicate YAML keys."""
    if f"{hook_name}:" in content:
        lines = content.splitlines()
        hook_idx = -1
        for idx, line in enumerate(lines):
            if line.strip() == f"{hook_name}:":
                hook_idx = idx
                break
        if hook_idx != -1:
            cmd_idx = -1
            for j in range(hook_idx + 1, len(lines)):
                line_j = lines[j]
                if line_j and not line_j.startswith((" ", "\t")):
                    break
                if line_j.strip() == "commands:":
                    cmd_idx = j
                    break
            if cmd_idx != -1:
                cmd_indent = " " * (len(lines[cmd_idx]) - len(lines[cmd_idx].lstrip()))
                sub_indent = cmd_indent + "  "
                run_indent = sub_indent + "  "
                injection = [
                    f"{sub_indent}vibetrace-sync:",
                    f"{run_indent}run: uv run vibetrace index && uv run vibetrace docs index",
                ]
                lines[cmd_idx + 1 : cmd_idx + 1] = injection
                return "\n".join(lines)
            injection = [
                "  commands:",
                "    vibetrace-sync:",
                "      run: uv run vibetrace index && uv run vibetrace docs index",
            ]
            lines[hook_idx + 1 : hook_idx + 1] = injection
            return "\n".join(lines)

    snippet = (
        f"\n{hook_name}:\n  commands:\n    vibetrace-sync:\n"
        "      run: uv run vibetrace index && uv run vibetrace docs index"
    )
    return content.rstrip() + snippet


def _clean_lefthook_content(content: str) -> str:
    """Safely strip VibeTrace commands and prune empty parent keys from lefthook.yml."""
    lines = content.splitlines()
    clean_lines: list[str] = []
    skip_indent: int | None = None
    for line in lines:
        stripped = line.strip()
        indent = len(line) - len(line.lstrip())
        if skip_indent is not None:
            if stripped and indent <= skip_indent:
                skip_indent = None
            else:
                continue
        if stripped == "vibetrace-sync:":
            skip_indent = indent
            continue
        clean_lines.append(line)
    result: list[str] = []
    i = 0
    while i < len(clean_lines):
        line = clean_lines[i]
        stripped = line.strip()
        if stripped == "commands:":
            has_children = False
            for j in range(i + 1, len(clean_lines)):
                next_l = clean_lines[j]
                if not next_l.strip():
                    continue
                indent = len(next_l) - len(next_l.lstrip())
                if indent > len(line) - len(line.lstrip()):
                    has_children = True
                break
            if not has_children:
                i += 1
                continue
        elif stripped in ("post-commit:", "post-merge:"):
            has_children = False
            for j in range(i + 1, len(clean_lines)):
                next_l = clean_lines[j]
                if not next_l.strip():
                    continue
                if next_l.startswith((" ", "\t")):
                    if next_l.strip() != "commands:":
                        has_children = True
                    else:
                        for k in range(j + 1, len(clean_lines)):
                            cmd_l = clean_lines[k]
                            if not cmd_l.strip():
                                continue
                            cmd_indent_len = len(cmd_l) - len(cmd_l.lstrip())
                            next_indent_len = len(next_l) - len(next_l.lstrip())
                            if cmd_indent_len > next_indent_len:
                                has_children = True
                            break
                break
            if not has_children:
                i += 1
                continue
        result.append(line)
        i += 1
    res = "\n".join(result).strip()
    return res + "\n" if res else ""


def install_lefthook_config(project_root: Path) -> tuple[bool, str]:
    """Append or merge post-commit and post-merge hooks into lefthook.yml."""
    root = project_root.resolve()
    lh_file = root / "lefthook.yml"
    if not lh_file.exists() and (root / ".lefthook.yml").exists():
        lh_file = root / ".lefthook.yml"

    existing_content = ""
    if lh_file.exists():
        try:
            existing_content = lh_file.read_text(encoding="utf-8")
        except OSError as e:
            return False, f"Failed to read {lh_file.name}: {e}"

    # Check each hook independently so a partially configured file still gets completed.
    already = [
        h
        for h in ("post-commit", "post-merge")
        if f"{h}:" in existing_content
        and ("vibetrace-sync:" in existing_content or "vibetrace index" in existing_content)
    ]
    if len(already) == 2:
        return True, f"VibeTrace hooks are already configured in {lh_file.name}."

    content = existing_content
    if "post-commit" not in already:
        content = _inject_lefthook_hook(content, "post-commit")
    if "post-merge" not in already:
        content = _inject_lefthook_hook(content, "post-merge")

    try:
        lh_file.write_text(content.strip() + "\n", encoding="utf-8")
    except OSError as e:
        return False, f"Failed to write to {lh_file.name}: {e}"

    # If lefthook CLI is present and this is an actual git repository, run lefthook install
    if shutil.which("lefthook") and (root / ".git").exists():
        try:
            result = subprocess.run(
                ["lefthook", "install"], cwd=str(root), check=False, capture_output=True, text=True
            )
            if result.returncode != 0:
                return False, (
                    "Updated lefthook.yml but 'lefthook install' failed: "
                    f"{result.stderr.strip() or result.stdout.strip()}"
                )
        except OSError as e:
            return False, f"Failed to run lefthook install: {e}"

    return True, f"Successfully appended post-commit & post-merge to {lh_file.name}."


def install_git_hooks(project_root: Path) -> tuple[bool, str]:
    """Install native Git hook scripts into .git/hooks/."""
    root = project_root.resolve()
    hooks_dir = _get_git_hooks_dir(root)
    if not hooks_dir:
        return False, f"Directory '{root}' is not a Git repository (.git not found or invalid)."

    try:
        hooks_dir.mkdir(parents=True, exist_ok=True)
    except OSError as e:
        return False, f"Failed to create hooks directory: {e}"

    installed_hooks: list[str] = []
    for hook_name in ("post-commit", "post-merge"):
        hook_path = hooks_dir / hook_name
        current_content = ""
        if hook_path.exists():
            try:
                current_content = hook_path.read_text(encoding="utf-8")
            except OSError as e:
                return False, f"Failed to read existing hook {hook_name}: {e}"

        if "vibetrace" in current_content:
            installed_hooks.append(f"{hook_name} (already present)")
            continue

        if not current_content.strip():
            final_content = f"#!/bin/sh\n{HOOK_SCRIPT_BLOCK}"
        else:
            final_content = current_content.rstrip() + "\n\n" + HOOK_SCRIPT_BLOCK

        try:
            hook_path.write_text(final_content, encoding="utf-8")
            if os.name != "nt":
                os.chmod(hook_path, 0o755)
            installed_hooks.append(hook_name)
        except OSError as e:
            return False, f"Failed to write hook {hook_name}: {e}"

    return True, f"Configured native Git hooks: {', '.join(installed_hooks)}."


def install_hooks(project_root: Path, provider: str = "auto") -> tuple[bool, str]:
    """
    Install automated indexing hooks for post-commit and post-merge.

    provider: 'auto', 'lefthook', or 'git'.
    """
    detected = detect_hook_provider(project_root) if provider == "auto" else provider

    if detected == "lefthook":
        return install_lefthook_config(project_root)
    if detected == "git":
        return install_git_hooks(project_root)
    return False, "Cannot auto-detect Git or Lefthook configuration in this project."


def uninstall_hooks(project_root: Path) -> tuple[bool, str]:
    """Safely remove VibeTrace hook commands from lefthook.yml and .git/hooks/."""
    root = project_root.resolve()
    removed_items: list[str] = []

    # 1. Clean lefthook.yml
    for lh_name in ("lefthook.yml", ".lefthook.yml"):
        lh_file = root / lh_name
        if lh_file.exists():
            try:
                content = lh_file.read_text(encoding="utf-8")
                if "vibetrace" in content:
                    cleaned = _clean_lefthook_content(content)
                    lh_file.write_text(cleaned, encoding="utf-8")
                    removed_items.append(f"removed from {lh_name}")
            except OSError:
                pass

    # 2. Clean .git/hooks
    git_hooks_dir = _get_git_hooks_dir(root)
    if git_hooks_dir and git_hooks_dir.exists():
        for hook_name in ("post-commit", "post-merge"):
            hook_path = git_hooks_dir / hook_name
            if hook_path.exists():
                try:
                    content = hook_path.read_text(encoding="utf-8")
                    if "vibetrace" in content:
                        start = content.find("# --- VibeTrace Automated Indexer ---")
                        end = content.find("# --- End VibeTrace ---")
                        marker_len = len("# --- End VibeTrace ---")
                        if start != -1 and end != -1:
                            cleaned = content[:start] + content[end + marker_len :]
                        else:
                            cleaned = "\n".join(
                                line for line in content.splitlines() if "vibetrace" not in line
                            )
                        hook_path.write_text(cleaned.strip() + "\n", encoding="utf-8")
                        removed_items.append(f"removed from hooks/{hook_name}")
                except OSError:
                    pass

    if not removed_items:
        return True, "No VibeTrace hooks found to uninstall."
    return True, f"Uninstalled VibeTrace hooks: {', '.join(removed_items)}."
