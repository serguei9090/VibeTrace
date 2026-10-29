"""Configuration engine for VibeTrace.

Manages project-level settings loaded from `.vibetrace.toml` at the project root
(or `vibetrace_context/config.toml` fallback), providing typed contracts for
databases, vector backends, and indexing behavior.
"""

from __future__ import annotations

import os
import tomllib
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field


class ProjectConfig(BaseModel):
    """Metadata identifying the project workspace."""

    name: str = Field(
        default="project",
        description="Project identifier (tenant partition key in central DB)",
    )


class DatabaseConfig(BaseModel):
    """Relational and AST graph database configuration."""

    backend: str = Field(
        default="sqlite",
        description="Storage backend: 'sqlite' (default/embedded). Future enterprise: 'postgres'",
    )
    path: str = Field(
        default=".vibetrace/vibetrace.db",
        description="Path to SQLite database file (relative to project root)",
    )
    busy_timeout_seconds: float = Field(
        default=30.0,
        ge=1.0,
        le=300.0,
        description="Busy timeout in seconds for SQLite multi-process write contention",
    )


class VectorConfig(BaseModel):
    """Vector database and embedding model configuration."""

    engine: str = Field(
        default="lancedb",
        description="Vector engine: 'lancedb' (embedded/cloud), 'sqlite-vec', or 'none'",
    )
    uri: str = Field(
        default=".vibetrace/vectors.lance",
        description="Path or URI for vector storage (local directory or 's3://bucket/path')",
    )
    model_id: str = Field(
        default="google/embeddinggemma-2",
        description="HuggingFace model identifier for symbol embeddings",
    )
    dimensions: int = Field(
        default=256,
        ge=64,
        le=768,
        description="Target Matryoshka vector dimension",
    )
    batch_size: int = Field(
        default=32,
        ge=1,
        le=512,
        description="Batch size for vector encoding",
    )


class IndexerConfig(BaseModel):
    """AST indexer and repository synchronization configuration."""

    debounce_seconds: float = Field(
        default=2.0,
        ge=0.0,
        le=60.0,
        description="Debounce window in seconds for Git-SHA delta sync checks (<20ms)",
    )
    auto_sync: bool = Field(
        default=True,
        description="Automatically synchronize working tree changes during queries",
    )
    exclude_patterns: list[str] = Field(
        default_factory=lambda: [
            "CodeIngestTest/**",
            "scratch/**",
            "tests/fixtures/**",
            "**/*.min.js",
            "**/*.bundle.js",
        ],
        description="Glob or gitignore patterns to exclude from AST indexing and graph analysis",
    )


class VibeTraceConfig(BaseModel):
    """Unified configuration contract for VibeTrace."""

    project: ProjectConfig = Field(default_factory=ProjectConfig)
    database: DatabaseConfig = Field(default_factory=DatabaseConfig)
    vectors: VectorConfig = Field(default_factory=VectorConfig)
    indexer: IndexerConfig = Field(default_factory=IndexerConfig)


def get_config_path(project_root: Path) -> Path | None:
    """Locate the configuration file using hierarchical discovery."""
    candidates = [
        project_root / ".vibetrace.toml",
        project_root / "vibetrace.toml",
        project_root / "vibetrace_context" / "config.toml",
        project_root / ".vibetrace" / "config.toml",
    ]
    for c in candidates:
        if c.is_file():
            return c
    return None


def generate_default_config_toml(project_name: str = "project") -> str:
    """Generate documented default TOML configuration."""
    safe_name = project_name.replace('"', '\\"')
    return f"""# VibeTrace Configuration Contract
# Resides at project root for easy discovery and team version-control.

[project]
# Project identifier (used as partition discriminator key for central databases)
name = "{safe_name}"

[database]
# Storage backend: "sqlite" (default). Future enterprise: "postgres"
backend = "sqlite"
# Local path for SQLite (stored safely inside .vibetrace/)
path = ".vibetrace/vibetrace.db"
# Connection busy timeout in seconds
busy_timeout_seconds = 30.0

[vectors]
# Vector engine: "lancedb" (recommended), "sqlite-vec", or "none"
engine = "lancedb"
# Local directory or remote cloud URI (e.g. "s3://company-vectors/{safe_name}")
uri = ".vibetrace/vectors.lance"
# Embedding model and Matryoshka dimension
model_id = "google/embeddinggemma-2"
dimensions = 256
batch_size = 32

[indexer]
# Working-tree Git-SHA delta debounce window (<20ms check)
debounce_seconds = 2.0
# Auto-sync working tree changes during queries
auto_sync = true
# Folders and glob patterns excluded from indexing
exclude_patterns = [
    "CodeIngestTest/**",
    "scratch/**",
    "tests/fixtures/**",
    "**/*.min.js",
]
"""


def load_config(project_root: Path | str | None = None) -> VibeTraceConfig:
    """Load VibeTrace configuration from project root or return defaults."""
    if project_root is None:
        env_root = os.environ.get("VIBETRACE_PROJECT_ROOT")
        root = Path(env_root).resolve() if env_root else Path.cwd().resolve()
    else:
        root = Path(project_root).resolve()

    if root.is_file():
        root = root.parent

    cfg_file = get_config_path(root)
    if not cfg_file or not cfg_file.exists():
        return VibeTraceConfig(project=ProjectConfig(name=root.name))

    raw_data: dict[str, Any] = {}
    if tomllib is not None:
        try:
            with cfg_file.open("rb") as f:
                raw_data = tomllib.load(f)
        except Exception:
            raw_data = {}

    cfg = VibeTraceConfig.model_validate(raw_data)
    if not raw_data.get("project", {}).get("name"):
        cfg.project.name = root.name
    return cfg


def save_default_config(project_root: Path | str, project_name: str | None = None) -> Path:
    """Create default .vibetrace.toml at project root if not already present."""
    root = Path(project_root).resolve()
    if root.is_file():
        root = root.parent
    target = root / ".vibetrace.toml"
    if not target.exists():
        name = project_name or root.name
        content = generate_default_config_toml(name)
        target.write_text(content, encoding="utf-8")
    return target
