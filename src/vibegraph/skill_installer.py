"""VibeGraph Skill & Workspace Initializer.

Configures .gitignore, installs the vibegraph-navigator skill for AI agents,
and sets up isolated project database environments.
"""

from __future__ import annotations

import importlib.resources
from pathlib import Path
from typing import Any

from vibegraph.indexer.db import IndexerDB
from vibegraph.indexer.main import reindex_all

VIBEGRAPH_IGNORE_PATTERNS = [
    "vibegraph.db",
    "vibegraph.db-wal",
    "vibegraph.db-shm",
    ".vibegraph/",
]


def get_bundled_skill_content() -> str:
    """Retrieve the bundled vibegraph-navigator SKILL.md from package resources."""
    try:
        ref = importlib.resources.files("vibegraph.resources").joinpath("SKILL.md")
        return ref.read_text(encoding="utf-8")
    except Exception:
        # Fallback to local file if running directly from source checkout
        src_path = (
            Path(__file__).resolve().parent.parent.parent
            / ".agents"
            / "skills"
            / "vibegraph-navigator"
            / "SKILL.md"
        )
        if src_path.exists():
            return src_path.read_text(encoding="utf-8")
        raise FileNotFoundError("Could not locate bundled vibegraph-navigator SKILL.md") from None


def configure_gitignore(project_root: Path | str) -> list[str]:
    """Ensure project .gitignore excludes local VibeGraph database and index files."""
    root = Path(project_root).resolve()
    gitignore_path = root / ".gitignore"
    existing_content = ""
    if gitignore_path.exists():
        try:
            existing_content = gitignore_path.read_text(encoding="utf-8")
        except Exception:
            existing_content = ""

    lines = [line.strip() for line in existing_content.splitlines()]
    added: list[str] = []

    for pattern in VIBEGRAPH_IGNORE_PATTERNS:
        clean_pat = pattern.rstrip("/")
        if pattern not in lines and clean_pat not in lines:
            added.append(pattern)

    if added:
        new_block = "\n# VibeGraph Code Intelligence\n" + "\n".join(added) + "\n"
        with gitignore_path.open("a", encoding="utf-8") as f:
            if existing_content and not existing_content.endswith("\n"):
                f.write("\n")
            f.write(new_block)

    return added


def install_skill(
    project_root: Path | str,
    global_install: bool = False,
) -> list[Path]:
    """Install the vibegraph-navigator skill into project or global agent directories."""
    content = get_bundled_skill_content()
    installed_paths: list[Path] = []

    if global_install:
        home = Path.home()
        global_dirs = [
            home / ".gemini" / "antigravity" / "skills" / "vibegraph-navigator",
            home / ".config" / "antigravity" / "skills" / "vibegraph-navigator",
        ]
        # Choose or create the first available global directory
        target_dir = global_dirs[0]
        target_dir.mkdir(parents=True, exist_ok=True)
        skill_file = target_dir / "SKILL.md"
        skill_file.write_text(content, encoding="utf-8")
        installed_paths.append(skill_file)
        return installed_paths

    root = Path(project_root).resolve()
    # Install into workspace .agents/skills/ (Standard Antigravity / Agent spec)
    agent_skill_dir = root / ".agents" / "skills" / "vibegraph-navigator"
    agent_skill_dir.mkdir(parents=True, exist_ok=True)
    agent_file = agent_skill_dir / "SKILL.md"
    agent_file.write_text(content, encoding="utf-8")
    installed_paths.append(agent_file)

    # If project uses .claude, also mirror to .claude/skills/
    claude_dir = root / ".claude"
    if claude_dir.is_dir():
        claude_skill_dir = claude_dir / "skills" / "vibegraph-navigator"
        claude_skill_dir.mkdir(parents=True, exist_ok=True)
        claude_file = claude_skill_dir / "SKILL.md"
        claude_file.write_text(content, encoding="utf-8")
        installed_paths.append(claude_file)

    return installed_paths


def initialize_workspace(
    project_root: Path | str,
    no_index: bool = False,
    global_skill: bool = False,
) -> dict[str, Any]:
    """Execute complete 1-click workspace initialization for VibeGraph."""
    root = Path(project_root).resolve()
    root.mkdir(parents=True, exist_ok=True)

    # 1. Update .gitignore
    added_ignores = configure_gitignore(root)

    # 2. Install navigator skill
    installed_skills = install_skill(root, global_install=global_skill)

    # 3. Create .vibegraph local directory if needed
    vibegraph_dir = root / ".vibegraph"
    vibegraph_dir.mkdir(exist_ok=True)

    # 4. Run initial indexing if requested
    indexed_nodes = 0
    indexed_edges = 0
    db_path = root / "vibegraph.db"

    if not no_index:
        db = IndexerDB(str(db_path))
        reindex_all(db, str(root), verbose=False, project_root=root)
        db.update_centrality_scores()
        graph_stats = db.get_graph_data()
        indexed_nodes = len(graph_stats.get("nodes", []))
        indexed_edges = len(graph_stats.get("edges", []))

    return {
        "project_root": str(root),
        "db_path": str(db_path),
        "gitignore_updated": bool(added_ignores),
        "added_ignores": added_ignores,
        "installed_skills": [str(p) for p in installed_skills],
        "indexed": not no_index,
        "node_count": indexed_nodes,
        "edge_count": indexed_edges,
    }
