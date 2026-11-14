"""Configuration management and manifest serialization for federated project workspaces."""

from __future__ import annotations

import json
import logging
import tomllib
from pathlib import Path

from vibetrace.federation.models import ProjectManifest, ProjectSettings, RepoMember

logger = logging.getLogger(__name__)

PROJECT_CONFIG_FILENAME = ".vibetrace-project.toml"

_SIGNIFICANT_MARKERS = (
    ".vibetrace",
    ".git",
    "pyproject.toml",
    "package.json",
    "Cargo.toml",
    "go.mod",
    "requirements.txt",
)


def find_project_root(start_path: Path | None = None) -> Path | None:
    """Traverse upward from start_path to locate the nearest .vibetrace-project.toml."""
    cur = (start_path or Path.cwd()).resolve()
    for parent in [cur, *cur.parents]:
        if (parent / PROJECT_CONFIG_FILENAME).is_file():
            return parent
    return None


def load_project_manifest(project_root: Path) -> ProjectManifest:
    """Load and parse the project workspace manifest from .vibetrace-project.toml."""
    manifest_file = project_root / PROJECT_CONFIG_FILENAME
    if not manifest_file.exists():
        raise FileNotFoundError(
            f"No federated project manifest found at '{manifest_file}'. "
            f"Run `vibetrace project init` to initialize a project workspace."
        )

    with open(manifest_file, "rb") as f:
        data = tomllib.load(f)

    project_raw = data.get("project", {})
    name = project_raw.get("name", project_root.name)
    version = project_raw.get("version", "1.0.0")
    description = project_raw.get("description", "")

    settings_raw = project_raw.get("settings", {})
    settings = ProjectSettings(
        storage_backend=settings_raw.get("storage_backend", "sqlite"),
        postgres_url=settings_raw.get("postgres_url"),
    )

    members: list[RepoMember] = []
    raw_members = project_raw.get("members", [])
    if not isinstance(raw_members, list):
        raise ValueError(f"Invalid 'members' field in '{manifest_file}': expected list")

    for idx, item in enumerate(raw_members):
        if not isinstance(item, dict) or "name" not in item or "path" not in item:
            raise ValueError(
                f"Invalid member at index {idx} in '{manifest_file}': 'name' & 'path' required"
            )
        members.append(
            RepoMember(
                name=str(item["name"]),
                path=str(item["path"]),
                description=str(item.get("description", "")),
                storage_path=str(item.get("storage_path", ".vibetrace/vibetrace.db")),
            )
        )

    return ProjectManifest(
        name=name,
        version=version,
        description=description,
        settings=settings,
        members=members,
    )


def save_project_manifest(project_root: Path, manifest: ProjectManifest) -> None:
    """Serialize and save the project manifest to .vibetrace-project.toml."""
    manifest_file = project_root / PROJECT_CONFIG_FILENAME
    lines: list[str] = [
        "# VibeTrace Federated Project Workspace Configuration",
        "# Generated automatically. Edit or add member repositories below.",
        "",
        "[project]",
        f"name = {json.dumps(manifest.name)}",
        f"version = {json.dumps(manifest.version)}",
        f"description = {json.dumps(manifest.description)}",
        "",
        "[project.settings]",
        f"storage_backend = {json.dumps(manifest.settings.storage_backend)}",
    ]
    if manifest.settings.postgres_url:
        lines.append(f"postgres_url = {json.dumps(manifest.settings.postgres_url)}")

    lines.append("")

    for member in manifest.members:
        lines.extend(
            [
                "[[project.members]]",
                f"name = {json.dumps(member.name)}",
                f"path = {json.dumps(member.path)}",
            ]
        )
        if member.description:
            lines.append(f"description = {json.dumps(member.description)}")
        if member.storage_path != ".vibetrace/vibetrace.db":
            lines.append(f"storage_path = {json.dumps(member.storage_path)}")
        lines.append("")

    manifest_file.write_text("\n".join(lines), encoding="utf-8")


def auto_discover_members(project_root: Path) -> list[RepoMember]:
    """Inspect immediate subdirectories of project_root for repository candidates."""
    candidates: list[RepoMember] = []
    try:
        entries = sorted(project_root.iterdir())
    except OSError as e:
        logger.warning("Failed to inspect directory %s for member repos: %s", project_root, e)
        return candidates

    for entry in entries:
        if (
            not entry.is_dir()
            or entry.name.startswith(".")
            or entry.name in ("node_modules", "target", "vendor", "dist", "build")
        ):
            continue

        is_repo = any((entry / marker).exists() for marker in _SIGNIFICANT_MARKERS)
        if is_repo:
            desc = ""
            if (entry / "package.json").exists():
                desc = "Node / TypeScript"
            elif (entry / "Cargo.toml").exists():
                desc = "Rust Crate"
            elif (entry / "go.mod").exists():
                desc = "Go Module"
            elif (entry / "pyproject.toml").exists() or (entry / "requirements.txt").exists():
                desc = "Python Package"

            candidates.append(
                RepoMember(
                    name=entry.name,
                    path=entry.name,
                    description=desc,
                )
            )

    return candidates
