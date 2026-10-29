"""Configuration management and manifest serialization for federated project workspaces."""

from __future__ import annotations

import tomllib
from pathlib import Path

from vibetrace.federation.models import ProjectManifest, ProjectSettings, RepoMember

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
    for item in project_raw.get("members", []):
        members.append(
            RepoMember(
                name=item["name"],
                path=item["path"],
                description=item.get("description", ""),
                storage_path=item.get("storage_path", ".vibetrace/vibetrace.db"),
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
        f'name = "{manifest.name}"',
        f'version = "{manifest.version}"',
        f'description = "{manifest.description}"',
        "",
        "[project.settings]",
        f'storage_backend = "{manifest.settings.storage_backend}"',
    ]
    if manifest.settings.postgres_url:
        lines.append(f'postgres_url = "{manifest.settings.postgres_url}"')

    lines.append("")

    for member in manifest.members:
        lines.append("[[project.members]]")
        lines.append(f'name = "{member.name}"')
        lines.append(f'path = "{member.path}"')
        if member.description:
            lines.append(f'description = "{member.description}"')
        if member.storage_path != ".vibetrace/vibetrace.db":
            lines.append(f'storage_path = "{member.storage_path}"')
        lines.append("")

    manifest_file.write_text("\n".join(lines), encoding="utf-8")


def auto_discover_members(project_root: Path) -> list[RepoMember]:
    """Inspect immediate subdirectories of project_root for repository candidates."""
    candidates: list[RepoMember] = []
    try:
        entries = sorted(project_root.iterdir())
    except Exception:
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
