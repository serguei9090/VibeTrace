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


class LinterConfigs(BaseModel):
    """Engine configuration selection: 'project' (root config), 'strict' (preset), or 'auto'."""

    ruff: str = Field(default="auto", description="'project' | 'strict' | 'auto'")
    biome: str = Field(default="auto", description="'project' | 'strict' | 'auto'")
    typescript: str = Field(default="auto", description="'project' | 'strict' | 'auto'")


class QualityConfig(BaseModel):
    """Quality rules synchronization and custom Sonar/remote feed configuration."""

    sonar_url: str = Field(
        default="http://localhost:9000",
        description="SonarQube Server URL for syncing or importing custom quality profiles",
    )
    sonar_token: str | None = Field(
        default=None,
        description="SonarQube authentication token (e.g. 'squ_...')",
    )
    rules_feed_url: str = Field(
        default=(
            "https://raw.githubusercontent.com/serguei9090/VibeTrace/main/"
            "src/vibetrace/resources/quality_rules_catalog.json"
        ),
        description="Remote JSON rules feed URL for community/enterprise catalog updates",
    )
    ttl_days: int = Field(
        default=7,
        ge=1,
        le=90,
        description="Local SQLite cache time-to-live for quality rules catalog in days",
    )
    linter_configs: LinterConfigs = Field(
        default_factory=LinterConfigs,
        description="Sub-engine configuration resolution preferences ('project' | 'strict')",
    )


class VibeTraceConfig(BaseModel):
    """Unified configuration contract for VibeTrace."""

    project: ProjectConfig = Field(default_factory=ProjectConfig)
    database: DatabaseConfig = Field(default_factory=DatabaseConfig)
    vectors: VectorConfig = Field(default_factory=VectorConfig)
    indexer: IndexerConfig = Field(default_factory=IndexerConfig)
    quality: QualityConfig = Field(default_factory=QualityConfig)


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

[quality]
# SonarQube Server URL & Auth token (optional, for custom company rule syncing)
# sonar_url = "http://localhost:9000"
# sonar_token = "squ_..."
# Remote JSON manifest feed URL (defaults to official VibeTrace GitHub repo)
# rules_feed_url = "https://raw.githubusercontent.com/serguei9090/VibeTrace/main/src/vibetrace/resources/quality_rules_catalog.json"
ttl_days = 7
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


def resolve_linter_mode(project_root: Path, engine: str) -> str:
    """Resolves whether an engine should use 'project' config or VibeTrace 'strict' preset."""
    cfg = load_config(project_root)
    val = getattr(cfg.quality.linter_configs, engine.lower(), "auto")
    if val in ("project", "strict"):
        return val

    # In 'auto' mode: check if project-level config file exists
    norm_engine = engine.lower()
    has_project_cfg = False
    if norm_engine == "ruff":
        has_project_cfg = any(
            (project_root / f).is_file() for f in ("ruff.toml", ".ruff.toml", "pyproject.toml")
        )
    elif norm_engine == "biome":
        has_project_cfg = any((project_root / f).is_file() for f in ("biome.json", "biome.jsonc"))
    elif norm_engine in ("typescript", "tsc"):
        has_project_cfg = any(
            (project_root / f).is_file()
            for f in ("tsconfig.json", "src/web/tsconfig.app.json", "src/web/tsconfig.json")
        )

    return "project" if has_project_cfg else "strict"


def save_linter_mode(project_root: Path, engine: str, mode: str) -> None:
    """Persists engine configuration choice into .vibetrace.toml."""
    cfg_file = get_config_path(project_root) or (project_root / ".vibetrace.toml")
    header_block = "[quality.linter_configs]\n"
    line_to_set = f'{engine.lower()} = "{mode}"\n'

    content = ""
    if cfg_file.is_file():
        content = cfg_file.read_text(encoding="utf-8", errors="replace")

    if "[quality.linter_configs]" in content:
        lines = content.splitlines(keepends=True)
        new_lines: list[str] = []
        in_section = False
        replaced = False
        for line in lines:
            s_line = line.strip()
            if s_line.startswith("[") and not s_line.startswith("[quality.linter_configs]"):
                if in_section and not replaced:
                    new_lines.append(line_to_set)
                    replaced = True
                in_section = False
            elif s_line == "[quality.linter_configs]":
                in_section = True
            elif in_section and s_line.startswith(f"{engine.lower()} ="):
                line = line_to_set
                replaced = True
            new_lines.append(line)
        if in_section and not replaced:
            new_lines.append(line_to_set)
        content = "".join(new_lines)
    else:
        content = content.rstrip() + f"\n\n{header_block}{line_to_set}"

    cfg_file.parent.mkdir(parents=True, exist_ok=True)
    cfg_file.write_text(content, encoding="utf-8")
