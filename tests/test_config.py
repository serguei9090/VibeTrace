"""Tests for VibeTrace configuration engine and .vibetrace.toml discovery."""

from __future__ import annotations

from pathlib import Path

from vibetrace.config import (
    VibeTraceConfig,
    load_config,
    save_default_config,
)
from vibetrace.skill_installer import initialize_workspace


def test_default_config_fallback(tmp_path: Path):
    """When no config file exists, load_config returns default configuration."""
    cfg = load_config(tmp_path)
    assert isinstance(cfg, VibeTraceConfig)
    assert cfg.project.name == tmp_path.name
    assert cfg.database.backend == "sqlite"
    assert cfg.database.path == ".vibetrace/vibetrace.db"
    assert cfg.vectors.engine == "lancedb"
    assert cfg.vectors.dimensions == 256
    assert cfg.indexer.auto_sync is True
    assert cfg.indexer.debounce_seconds == 2.0


def test_save_and_load_default_config(tmp_path: Path):
    """save_default_config creates .vibetrace.toml and load_config parses it."""
    cfg_file = save_default_config(tmp_path, project_name="my-service")
    assert cfg_file.exists()
    assert cfg_file.name == ".vibetrace.toml"

    content = cfg_file.read_text(encoding="utf-8")
    assert 'name = "my-service"' in content
    assert 'engine = "lancedb"' in content

    # Reload and verify
    cfg = load_config(tmp_path)
    assert cfg.project.name == "my-service"
    assert cfg.database.backend == "sqlite"
    assert cfg.vectors.engine == "lancedb"


def test_custom_config_overrides(tmp_path: Path):
    """Custom settings in .vibetrace.toml correctly override defaults."""
    custom_toml = """
[project]
name = "custom-auth-service"

[database]
backend = "sqlite"
path = "custom_dir/custom.db"
busy_timeout_seconds = 45.0

[vectors]
engine = "sqlite-vec"
uri = "s3://team-bucket/vectors"
model_id = "custom/model-large"
dimensions = 512
batch_size = 64

[indexer]
debounce_seconds = 5.0
auto_sync = false
"""
    (tmp_path / ".vibetrace.toml").write_text(custom_toml, encoding="utf-8")

    cfg = load_config(tmp_path)
    assert cfg.project.name == "custom-auth-service"
    assert cfg.database.path == "custom_dir/custom.db"
    assert cfg.database.busy_timeout_seconds == 45.0
    assert cfg.vectors.engine == "sqlite-vec"
    assert cfg.vectors.uri == "s3://team-bucket/vectors"
    assert cfg.vectors.model_id == "custom/model-large"
    assert cfg.vectors.dimensions == 512
    assert cfg.vectors.batch_size == 64
    assert cfg.indexer.debounce_seconds == 5.0
    assert cfg.indexer.auto_sync is False


def test_context_folder_config_fallback(tmp_path: Path):
    """If root .vibetrace.toml is absent, vibetrace_context/config.toml is read."""
    ctx_dir = tmp_path / "vibetrace_context"
    ctx_dir.mkdir()
    (ctx_dir / "config.toml").write_text(
        """
[project]
name = "context-repo"

[vectors]
dimensions = 128
""",
        encoding="utf-8",
    )

    cfg = load_config(tmp_path)
    assert cfg.project.name == "context-repo"
    assert cfg.vectors.dimensions == 128


def test_initialize_workspace_creates_config(tmp_path: Path):
    """initialize_workspace generates .vibetrace.toml and returns its path."""
    res = initialize_workspace(tmp_path, no_index=True)
    assert "config_path" in res
    assert Path(res["config_path"]).exists()
    assert Path(res["config_path"]).name == ".vibetrace.toml"

    cfg = load_config(tmp_path)
    assert cfg.project.name == tmp_path.name
