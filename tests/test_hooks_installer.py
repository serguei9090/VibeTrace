"""Unit tests for automated Git hook and Lefthook installation/uninstallation."""

from __future__ import annotations

from pathlib import Path

from vibetrace.cli import main
from vibetrace.indexer.hooks import (
    detect_hook_provider,
    get_hooks_status,
    install_git_hooks,
    install_lefthook_config,
    uninstall_hooks,
)


def test_detect_hook_provider(tmp_path: Path) -> None:
    """Verify correct provider detection for Lefthook, native Git, and unversioned folders."""
    # 1. Empty folder
    assert detect_hook_provider(tmp_path) == "none"

    # 2. Git repo
    (tmp_path / ".git").mkdir()
    assert detect_hook_provider(tmp_path) == "git"

    # 3. Lefthook repo
    (tmp_path / "lefthook.yml").write_text("pre-commit:\n  commands: {}\n")
    assert detect_hook_provider(tmp_path) == "lefthook"


def test_install_lefthook_lifecycle(tmp_path: Path) -> None:
    """Verify appending post-commit & post-merge to lefthook.yml and clean removal."""
    lh_file = tmp_path / "lefthook.yml"
    lh_file.write_text("pre-commit:\n  commands:\n    lint:\n      run: ruff check\n")

    # Install
    ok, _msg = install_lefthook_config(tmp_path)
    assert ok is True
    content = lh_file.read_text()
    assert "post-commit:" in content
    assert "post-merge:" in content
    assert "vibetrace-sync" in content
    assert "pre-commit:" in content

    # Idempotent install
    ok_second, msg_second = install_lefthook_config(tmp_path)
    assert ok_second is True
    assert "already configured" in msg_second

    # Inspect status
    st = get_hooks_status(tmp_path)
    assert st["lefthook"]["configured"] is True

    # Uninstall
    ok_un, _msg_un = uninstall_hooks(tmp_path)
    assert ok_un is True
    content_after = lh_file.read_text()
    assert "vibetrace" not in content_after
    assert "pre-commit:" in content_after


def test_install_git_hooks_lifecycle(tmp_path: Path) -> None:
    """Verify native Git hook script generation into .git/hooks and clean removal."""
    git_dir = tmp_path / ".git"
    git_dir.mkdir()

    # Install
    ok, _msg = install_git_hooks(tmp_path)
    assert ok is True

    hooks_dir = git_dir / "hooks"
    pc = hooks_dir / "post-commit"
    pm = hooks_dir / "post-merge"
    assert pc.exists()
    assert pm.exists()
    assert "vibetrace" in pc.read_text()
    assert "vibetrace" in pm.read_text()

    # Idempotent install
    ok_second, _msg_second = install_git_hooks(tmp_path)
    assert ok_second is True

    # Inspect status
    st = get_hooks_status(tmp_path)
    assert st["git"]["post_commit"] is True
    assert st["git"]["post_merge"] is True

    # Uninstall
    ok_un, _msg_un = uninstall_hooks(tmp_path)
    assert ok_un is True
    assert "vibetrace" not in pc.read_text()
    assert "vibetrace" not in pm.read_text()


def test_cli_hooks_commands(tmp_path: Path) -> None:
    """Test CLI commands: vibetrace hooks status, install, and uninstall."""
    (tmp_path / ".git").mkdir()

    # CLI status
    assert main(["hooks", "status", "--path", str(tmp_path)]) == 0

    # CLI install
    assert main(["hooks", "install", "--path", str(tmp_path), "--provider", "git"]) == 0

    # CLI status (verified)
    assert main(["hooks", "status", "--path", str(tmp_path), "--json"]) == 0

    # CLI uninstall
    assert main(["hooks", "uninstall", "--path", str(tmp_path)]) == 0
