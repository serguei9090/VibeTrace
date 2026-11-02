"""Unit tests for linter config resolution and switcher (.vibetrace.toml)."""

from __future__ import annotations

from pathlib import Path

from vibetrace.config import (
    load_config,
    resolve_linter_mode,
    save_linter_mode,
)
from vibetrace.quality.adapters.biome import BiomeAdapter
from vibetrace.quality.adapters.python_ruff import RuffAdapter


def test_resolve_linter_mode_auto_with_project_configs(tmp_path: Path) -> None:
    # No config file present -> defaults to strict
    assert resolve_linter_mode(tmp_path, "biome") == "strict"
    assert resolve_linter_mode(tmp_path, "ruff") == "strict"

    # When project config file exists -> auto resolves to project
    (tmp_path / "biome.json").write_text("{}", encoding="utf-8")
    assert resolve_linter_mode(tmp_path, "biome") == "project"

    (tmp_path / "ruff.toml").write_text("", encoding="utf-8")
    assert resolve_linter_mode(tmp_path, "ruff") == "project"


def test_save_and_load_linter_mode(tmp_path: Path) -> None:
    save_linter_mode(tmp_path, "biome", "strict")
    save_linter_mode(tmp_path, "ruff", "project")

    cfg = load_config(tmp_path)
    assert cfg.quality.linter_configs.biome == "strict"
    assert cfg.quality.linter_configs.ruff == "project"
    assert resolve_linter_mode(tmp_path, "biome") == "strict"
    assert resolve_linter_mode(tmp_path, "ruff") == "project"


def test_adapters_respect_saved_linter_mode(tmp_path: Path) -> None:
    # 1. Biome with 'project' mode
    proj_biome = tmp_path / "biome.json"
    proj_biome.write_text('{"linter": {"enabled": true}}', encoding="utf-8")
    save_linter_mode(tmp_path, "biome", "project")

    b_adapter = BiomeAdapter()
    resolved_b = b_adapter._resolve_config_path(tmp_path)
    assert resolved_b == proj_biome

    # 2. Switch Biome to 'strict' mode -> returns bundled preset
    save_linter_mode(tmp_path, "biome", "strict")
    resolved_strict = b_adapter._resolve_config_path(tmp_path)
    assert resolved_strict.name == "biome_sonar.json"

    # 3. Ruff with 'project' mode
    proj_ruff = tmp_path / "ruff.toml"
    proj_ruff.write_text("[lint]\nselect = ['E']", encoding="utf-8")
    save_linter_mode(tmp_path, "ruff", "project")

    r_adapter = RuffAdapter()
    resolved_r = r_adapter._resolve_config_path(tmp_path)
    assert resolved_r == proj_ruff
