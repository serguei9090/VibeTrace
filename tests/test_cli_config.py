"""Unit tests for vibetrace config CLI command."""

from __future__ import annotations

import argparse
from pathlib import Path

from vibetrace.cli_config import cmd_config_quality
from vibetrace.config import resolve_linter_mode


def test_cmd_config_quality_display(tmp_path: Path, capsys) -> None:
    """Verifies cmd_config_quality prints formatted markdown status table."""
    args = argparse.Namespace(path=str(tmp_path), set_opt=None)
    code = cmd_config_quality(args)
    assert code == 0

    captured = capsys.readouterr().out
    assert "# ⚙️ VibeTrace Quality Sub-Engine Configurations" in captured
    assert "Ruff (Python)" in captured
    assert "Biome (JS/TS/JSON/CSS)" in captured
    assert "TypeScript (tsc)" in captured


def test_cmd_config_quality_set_mode(tmp_path: Path) -> None:
    """Verifies cmd_config_quality updates preset modes in .vibetrace.toml."""
    args = argparse.Namespace(path=str(tmp_path), set_opt="ruff=strict")
    code = cmd_config_quality(args)
    assert code == 0
    assert resolve_linter_mode(tmp_path, "ruff") == "strict"

    args = argparse.Namespace(path=str(tmp_path), set_opt="biome=project")
    code = cmd_config_quality(args)
    assert code == 0
    assert resolve_linter_mode(tmp_path, "biome") == "project"

    args = argparse.Namespace(path=str(tmp_path), set_opt="tsc=strict")
    code = cmd_config_quality(args)
    assert code == 0
    assert resolve_linter_mode(tmp_path, "typescript") == "strict"


def test_cmd_config_quality_invalid_inputs(tmp_path: Path, capsys) -> None:
    """Verifies error handling on bad formatting, unknown engine, or invalid mode."""
    # Bad format (no =)
    code = cmd_config_quality(argparse.Namespace(path=str(tmp_path), set_opt="ruff_strict"))
    assert code == 1
    assert "Format must be KEY=VALUE" in capsys.readouterr().out

    # Unknown engine
    code = cmd_config_quality(argparse.Namespace(path=str(tmp_path), set_opt="unknown=strict"))
    assert code == 1
    assert "Unknown sub-engine" in capsys.readouterr().out

    # Invalid mode
    code = cmd_config_quality(argparse.Namespace(path=str(tmp_path), set_opt="ruff=invalid"))
    assert code == 1
    assert "Invalid mode" in capsys.readouterr().out
