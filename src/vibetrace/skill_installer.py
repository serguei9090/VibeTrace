"""VibeTrace Skill & Workspace Initializer.

Configures .gitignore, installs the vibetrace-navigator skill for AI agents,
and sets up isolated project database environments.
"""

from __future__ import annotations

import importlib.resources
from pathlib import Path
from typing import Any

from vibetrace.indexer.db import IndexerDB
from vibetrace.indexer.main import reindex_all


def detect_project_ecosystems(project_root: Path) -> list[str]:
    """Detect active project ecosystems and runtime toolchains."""
    ecosystems: list[str] = []
    root = project_root.resolve()
    if (root / "pyproject.toml").exists() or (root / "requirements.txt").exists():
        uv_hint = " (uv)" if (root / "uv.lock").exists() else ""
        ecosystems.append(f"Python{uv_hint}")
    if (root / "package.json").exists():
        bun_hint = " (bun)" if (root / "bun.lock").exists() or (root / "bun.lockb").exists() else ""
        ecosystems.append(f"JavaScript/TypeScript{bun_hint}")
    if (root / "Cargo.toml").exists():
        ecosystems.append("Rust (cargo)")
    if (root / "go.mod").exists():
        ecosystems.append("Go (modules)")
    return ecosystems


VIBETRACE_IGNORE_PATTERNS = [
    "vibetrace.db",
    "vibetrace.db-wal",
    "vibetrace.db-shm",
    ".vibetrace/",
]


def get_bundled_skill_content() -> str:
    """Retrieve the bundled vibetrace-navigator SKILL.md from package resources."""
    try:
        ref = importlib.resources.files("vibetrace.resources").joinpath("SKILL.md")
        return ref.read_text(encoding="utf-8")
    except Exception:
        # Fallback to local file if running directly from source checkout
        src_path = (
            Path(__file__).resolve().parent.parent.parent
            / ".agents"
            / "skills"
            / "vibetrace-navigator"
            / "SKILL.md"
        )
        if src_path.exists():
            return src_path.read_text(encoding="utf-8")
        raise FileNotFoundError("Could not locate bundled vibetrace-navigator SKILL.md") from None


def configure_gitignore(project_root: Path | str) -> list[str]:
    """Ensure project .gitignore excludes local VibeTrace database and index files."""
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

    for pattern in VIBETRACE_IGNORE_PATTERNS:
        clean_pat = pattern.rstrip("/")
        if pattern not in lines and clean_pat not in lines:
            added.append(pattern)

    if added:
        new_block = "\n# VibeTrace Code Intelligence\n" + "\n".join(added) + "\n"
        with gitignore_path.open("a", encoding="utf-8") as f:
            if existing_content and not existing_content.endswith("\n"):
                f.write("\n")
            f.write(new_block)

    return added


def install_skill(
    project_root: Path | str,
    global_install: bool = False,
) -> list[Path]:
    """Install the vibetrace-navigator skill into project or global agent directories."""
    content = get_bundled_skill_content()
    installed_paths: list[Path] = []

    if global_install:
        home = Path.home()
        global_dirs = [
            home / ".gemini" / "antigravity" / "skills" / "vibetrace-navigator",
            home / ".config" / "antigravity" / "skills" / "vibetrace-navigator",
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
    agent_skill_dir = root / ".agents" / "skills" / "vibetrace-navigator"
    agent_skill_dir.mkdir(parents=True, exist_ok=True)
    agent_file = agent_skill_dir / "SKILL.md"
    agent_file.write_text(content, encoding="utf-8")
    installed_paths.append(agent_file)

    # If project uses .claude, also mirror to .claude/skills/
    claude_dir = root / ".claude"
    if claude_dir.is_dir():
        claude_skill_dir = claude_dir / "skills" / "vibetrace-navigator"
        claude_skill_dir.mkdir(parents=True, exist_ok=True)
        claude_file = claude_skill_dir / "SKILL.md"
        claude_file.write_text(content, encoding="utf-8")
        installed_paths.append(claude_file)

    return installed_paths


def initialize_workspace(
    project_root: Path | str,
    no_index: bool = False,
    global_skill: bool = False,
    configure_hooks: bool = False,
) -> dict[str, Any]:
    """Execute complete 1-click workspace initialization for VibeTrace."""
    root = Path(project_root).resolve()
    root.mkdir(parents=True, exist_ok=True)

    # 1. Update .gitignore
    added_ignores = configure_gitignore(root)

    # 2. Install navigator skill
    installed_skills = install_skill(root, global_install=global_skill)

    # 3. Create root .vibetrace.toml configuration file
    from vibetrace.config import save_default_config

    config_path = save_default_config(root, project_name=root.name)

    # 4. Create .vibetrace local directory if needed
    vibetrace_dir = root / ".vibetrace"
    vibetrace_dir.mkdir(exist_ok=True)

    # 5. Ecosystem & toolchain detection
    detected_ecosystems = detect_project_ecosystems(root)

    # 6. Auto-register docs catalog if docs/ directory exists
    doc_catalog_registered = False
    doc_chunks_indexed = 0
    db_path = vibetrace_dir / "vibetrace.db"
    docs_dir = root / "docs"
    if docs_dir.is_dir():
        try:
            from vibetrace.docs.db import DocDB
            from vibetrace.docs.service import DocCatalogManager, DocIndexService

            DocCatalogManager.add_path("core", "docs", project_root=root)
            doc_catalog_registered = True
            if not no_index:
                doc_db = DocDB(db_path)
                res_docs = DocIndexService.index_catalog("core", ["docs"], doc_db, root)
                doc_chunks_indexed = res_docs.get("chunks", 0)
        except Exception:
            doc_catalog_registered = False

    # 7. Configure Git / Lefthook hooks if requested
    hooks_installed = False
    hooks_message = ""
    if configure_hooks:
        try:
            from vibetrace.indexer.hooks import install_hooks

            hooks_installed, hooks_message = install_hooks(root, provider="auto")
        except Exception as e:
            hooks_installed = False
            hooks_message = str(e)

    # 8. Run initial indexing if requested
    indexed_nodes = 0
    indexed_edges = 0

    if not no_index:
        db = IndexerDB(str(db_path))
        reindex_all(db, str(root), verbose=False, project_root=root)
        db.update_centrality_scores()
        graph_stats = db.get_graph_data()
        indexed_nodes = len(graph_stats.get("nodes", []))
        indexed_edges = len(graph_stats.get("edges", []))
        try:
            from vibetrace.security.rule_sync import RuleCatalogManager

            RuleCatalogManager.sync_and_load_rules(db, root)
        except Exception:
            pass

    return {
        "project_root": str(root),
        "db_path": str(db_path),
        "config_path": str(config_path),
        "gitignore_updated": bool(added_ignores),
        "added_ignores": added_ignores,
        "installed_skills": [str(p) for p in installed_skills],
        "ecosystems": detected_ecosystems,
        "doc_catalog_registered": doc_catalog_registered,
        "doc_chunks_indexed": doc_chunks_indexed,
        "hooks_installed": hooks_installed,
        "hooks_message": hooks_message,
        "indexed": not no_index,
        "node_count": indexed_nodes,
        "edge_count": indexed_edges,
    }
