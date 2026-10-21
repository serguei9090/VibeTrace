"""Tests for workspace initialization, .gitignore configuration, and skill installation."""

from __future__ import annotations

import json
from pathlib import Path

from vibegraph.cli import main
from vibegraph.skill_installer import (
    configure_gitignore,
    get_bundled_skill_content,
    initialize_workspace,
    install_skill,
)


def test_get_bundled_skill_content() -> None:
    """Verify bundled SKILL.md can be read and contains core workflow instructions."""
    content = get_bundled_skill_content()
    assert "# VibeGraph Navigator Skill" in content
    assert "vibegraph-navigator" in content
    assert "Step 1: Locate Symbols" in content


def test_configure_gitignore_idempotent(tmp_path: Path) -> None:
    """Verify configure_gitignore adds missing patterns and avoids duplicates on re-run."""
    # 1. Clean directory without .gitignore
    added = configure_gitignore(tmp_path)
    assert len(added) == 4
    assert "vibegraph.db" in added
    assert ".vibegraph/" in added

    content = (tmp_path / ".gitignore").read_text(encoding="utf-8")
    assert "vibegraph.db" in content
    assert "vibegraph.db-wal" in content

    # 2. Re-run: should be a no-op (no duplicates)
    added_second = configure_gitignore(tmp_path)
    assert len(added_second) == 0

    # 3. Existing .gitignore with pre-existing rules
    other_path = tmp_path / "subdir"
    other_path.mkdir()
    (other_path / ".gitignore").write_text("node_modules/\n.env\n", encoding="utf-8")
    added_third = configure_gitignore(other_path)
    assert len(added_third) == 4

    content_third = (other_path / ".gitignore").read_text(encoding="utf-8")
    assert "node_modules/" in content_third
    assert "vibegraph.db" in content_third


def test_install_skill_workspace(tmp_path: Path) -> None:
    """Verify install_skill writes SKILL.md into .agents/skills/vibegraph-navigator."""
    installed = install_skill(tmp_path)
    assert len(installed) >= 1
    skill_file = tmp_path / ".agents" / "skills" / "vibegraph-navigator" / "SKILL.md"
    assert skill_file.is_file()
    assert "# VibeGraph Navigator Skill" in skill_file.read_text(encoding="utf-8")


def test_install_skill_mirrors_claude_when_present(tmp_path: Path) -> None:
    """Verify install_skill also populates .claude/skills if .claude directory exists."""
    (tmp_path / ".claude").mkdir()
    installed = install_skill(tmp_path)
    assert len(installed) == 2
    claude_skill = tmp_path / ".claude" / "skills" / "vibegraph-navigator" / "SKILL.md"
    assert claude_skill.is_file()


def test_initialize_workspace_e2e(tmp_path: Path) -> None:
    """Verify full initialize_workspace sets up gitignore, skills, and local db."""
    # Create sample source file
    src_dir = tmp_path / "src"
    src_dir.mkdir()
    (src_dir / "calculator.py").write_text(
        "def add(a: int, b: int) -> int:\n    return a + b\n",
        encoding="utf-8",
    )

    res = initialize_workspace(tmp_path)
    assert res["gitignore_updated"] is True
    assert (tmp_path / ".gitignore").is_file()
    assert (tmp_path / ".agents" / "skills" / "vibegraph-navigator" / "SKILL.md").is_file()

    # Local database must reside in tmp_path, completely isolated
    local_db = tmp_path / "vibegraph.db"
    assert local_db.is_file()
    assert res["node_count"] >= 1


def test_cli_init_command(tmp_path: Path, capsys) -> None:
    """Verify CLI `vibegraph init <path> --no-index --json` runs cleanly."""
    exit_code = main(["init", str(tmp_path), "--no-index", "--json"])
    assert exit_code == 0
    captured = capsys.readouterr()
    payload = json.loads(captured.out)
    assert payload["indexed"] is False
    assert (tmp_path / ".gitignore").is_file()
    assert (tmp_path / ".agents" / "skills" / "vibegraph-navigator" / "SKILL.md").is_file()


def test_cli_skill_install_command(tmp_path: Path, capsys) -> None:
    """Verify CLI `vibegraph skill install --path <path> --json` runs cleanly."""
    exit_code = main(["skill", "install", "--path", str(tmp_path), "--json"])
    assert exit_code == 0
    captured = capsys.readouterr()
    payload = json.loads(captured.out)
    assert payload["status"] == "ok"
    assert len(payload["installed_paths"]) >= 1
