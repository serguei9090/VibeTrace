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
    """Detect active project ecosystems and runtime toolchains (root + 1-level subdirs)."""
    ecosystems: list[str] = []
    root = project_root.resolve()
    candidates = [root] + [
        p
        for p in root.iterdir()
        if p.is_dir()
        and not p.name.startswith(".")
        and p.name not in ("node_modules", "target", "build", "dist", "vendor")
    ]
    if any(
        (d / "pyproject.toml").exists() or (d / "requirements.txt").exists() for d in candidates
    ):
        uv_hint = " (uv)" if any((d / "uv.lock").exists() for d in candidates) else ""
        ecosystems.append(f"Python{uv_hint}")
    if any((d / "package.json").exists() for d in candidates):
        bun_hint = (
            " (bun)"
            if any((d / "bun.lock").exists() or (d / "bun.lockb").exists() for d in candidates)
            else ""
        )
        ecosystems.append(f"JavaScript/TypeScript{bun_hint}")
    if any((d / "Cargo.toml").exists() for d in candidates):
        ecosystems.append("Rust (cargo)")
    if any((d / "go.mod").exists() for d in candidates):
        ecosystems.append("Go (modules)")
    if any((d / "pubspec.yaml").exists() for d in candidates):
        ecosystems.append("Flutter/Dart")
    return ecosystems


def detect_project_languages(project_root: Path) -> list[str]:
    """Detect canonical LSP language keys present in a repository (root + shallow probes)."""
    root = project_root.resolve()
    ecosystems = detect_project_ecosystems(root)
    langs: list[str] = []

    checks = (
        (
            "python",
            any("Python" in e for e in ecosystems)
            or any(root.glob("*.py"))
            or any(root.glob("*/*.py")),
        ),
        (
            "typescript",
            any("JavaScript/TypeScript" in e for e in ecosystems)
            or (root / "tsconfig.json").exists()
            or any(root.glob("*.ts"))
            or any(root.glob("*.tsx"))
            or any(root.glob("*.js"))
            or any(root.glob("*.jsx"))
            or any(root.glob("*/*.ts")),
        ),
        (
            "go",
            any("Go" in e for e in ecosystems)
            or any(root.glob("*.go"))
            or any(root.glob("*/*.go")),
        ),
        (
            "rust",
            any("Rust" in e for e in ecosystems)
            or any(root.glob("*.rs"))
            or any(root.glob("src/*.rs")),
        ),
        (
            "php",
            any(root.glob("*.php"))
            or any(root.glob("*/*.php"))
            or (root / "composer.json").exists(),
        ),
        ("c", any(root.glob("*.c")) or any(root.glob("*/*.c"))),
        (
            "cpp",
            any(root.glob("*.cpp"))
            or any(root.glob("*.cc"))
            or any(root.glob("*/*.cpp"))
            or any(root.glob("*/*.cc"))
            or (root / "CMakeLists.txt").exists()
            or (root / "compile_commands.json").exists(),
        ),
        (
            "csharp",
            any(root.glob("*.cs"))
            or any(root.glob("*.sln"))
            or any(root.glob("*.csproj"))
            or any(root.glob("*/*.cs"))
            or any(root.glob("*/*.csproj"))
            or any(root.glob("src/*/*.csproj")),
        ),
        (
            "ruby",
            any(root.glob("*.rb"))
            or any(root.glob("*/*.rb"))
            or (root / "Gemfile").exists()
            or any(root.glob("*.gemspec")),
        ),
        (
            "java",
            any(root.glob("*.java"))
            or any(root.glob("*/*.java"))
            or (root / "pom.xml").exists()
            or (root / "build.gradle").exists()
            or (root / "build.gradle.kts").exists(),
        ),
    )
    for lang, matched in checks:
        if matched:
            langs.append(lang)
    return langs


VIBETRACE_IGNORE_PATTERNS = [
    "vibetrace.db",
    "vibetrace.db-wal",
    "vibetrace.db-shm",
    ".vibetrace/*",
    "!.vibetrace/splits.toml",
    "!.vibetrace/security.toml",
    "!.vibetrace/quality.toml",
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
    custom_skills_dir: Path | str | None = None,
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

    if custom_skills_dir:
        custom_dir = Path(custom_skills_dir)
        if not custom_dir.is_absolute():
            custom_dir = root / custom_dir
        target_skill_dir = custom_dir / "vibetrace-navigator"
        target_skill_dir.mkdir(parents=True, exist_ok=True)
        skill_file = target_skill_dir / "SKILL.md"
        skill_file.write_text(content, encoding="utf-8")
        installed_paths.append(skill_file)
        return installed_paths

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


def _init_docs_catalog(root: Path, db_path: Path, no_index: bool) -> tuple[bool, int]:
    """Auto-registers and indexes core docs catalog if docs/ exists."""
    try:
        from vibetrace.config import load_config
        from vibetrace.docs.db import DocDB
        from vibetrace.docs.service import (
            DocCatalogManager,
            DocIndexService,
            resolve_default_docs_dir,
        )

        pref_loc = load_config(root).docs.preferred_locale
        docs_rel = resolve_default_docs_dir(root, pref_loc)
        if not docs_rel:
            return False, 0

        DocCatalogManager.add_path("core", docs_rel, project_root=root)
        doc_chunks = 0
        if not no_index:
            doc_db = DocDB(db_path)
            res_docs = DocIndexService.index_catalog("core", [docs_rel], doc_db, root)
            doc_chunks = res_docs.get("chunks", 0)
        return True, doc_chunks
    except Exception as exc:
        import logging

        logging.getLogger(__name__).warning("Failed to initialize docs catalog: %s", exc)
        return False, 0


def _run_initial_indexing(db_path: Path, root: Path, use_lsp: bool = True) -> tuple[int, int, int]:
    """Runs initial AST + LSP indexing and rule catalog synchronization."""
    db = IndexerDB(str(db_path))
    reindex_all(db, str(root), verbose=False, project_root=root, use_lsp=use_lsp)
    db.prune_orphaned_edges()
    db.update_centrality_scores()
    stats = db.get_statistics()
    indexed_nodes = int(stats.get("nodes", 0))
    indexed_edges = int(stats.get("edges", 0))
    try:
        from vibetrace.security.rule_sync import RuleCatalogManager

        RuleCatalogManager.sync_and_load_rules(db, root)
    except Exception:
        pass

    from vibetrace.quality.rules import QUALITY_RULES_CATALOG

    return indexed_nodes, indexed_edges, len(QUALITY_RULES_CATALOG)


def initialize_workspace(
    project_root: Path | str,
    no_index: bool = False,
    global_skill: bool = False,
    configure_hooks: bool = False,
    include_tests: bool = False,
    codebase_embeddings: bool = False,
    compute_device: str = "gpu",
    enable_sonar: bool = False,
    enable_semgrep: bool = True,
    enable_trivy: bool = True,
    custom_skills_dir: Path | str | None = None,
) -> dict[str, Any]:
    """Execute complete 1-click workspace initialization for VibeTrace."""
    root = Path(project_root).resolve()
    root.mkdir(parents=True, exist_ok=True)

    # 1. Install navigator skill first
    installed_skills = install_skill(
        root, global_install=global_skill, custom_skills_dir=custom_skills_dir
    )

    # 2. Update .gitignore
    added_ignores = configure_gitignore(root)

    # 3. Create root .vibetrace.toml configuration file
    from vibetrace.config import save_default_config

    detected_languages = detect_project_languages(root)
    config_path = save_default_config(
        root,
        project_name=root.name,
        include_tests=include_tests,
        codebase_embeddings=codebase_embeddings,
        compute_device=compute_device,
        enable_sonar=enable_sonar,
        enable_semgrep=enable_semgrep,
        enable_trivy=enable_trivy,
        detected_languages=detected_languages,
    )

    # 4. Create .vibetrace local directory if needed
    vibetrace_dir = root / ".vibetrace"
    vibetrace_dir.mkdir(exist_ok=True)
    quality_baseline = vibetrace_dir / "quality.toml"
    if not quality_baseline.exists():
        quality_baseline.write_text(
            "# .vibetrace/quality.toml\n"
            "# Code quality exceptions and approved complexity baseline.\n\n"
            '[quality]\nversion = "1.0"\ndefault_complexity_threshold = 15\n\n',
            encoding="utf-8",
        )

    # 5. Ecosystem & toolchain detection
    detected_ecosystems = detect_project_ecosystems(root)

    # 6. Auto-register docs catalog if docs/ directory exists
    db_path = vibetrace_dir / "vibetrace.db"
    doc_catalog_registered, doc_chunks_indexed = _init_docs_catalog(root, db_path, no_index)

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

    # 7b. Detect container & Kubernetes security tooling + Semgrep CLI
    try:
        from vibetrace.security.adapters.semgrep import SemgrepAdapter
        from vibetrace.security.adapters.trivy import TrivyAdapter

        has_containers = TrivyAdapter.has_container_configs(root)
        trivy_available = TrivyAdapter.is_available()
        semgrep_available = SemgrepAdapter.is_available()
    except Exception:
        has_containers = False
        trivy_available = False
        semgrep_available = False

    # 8. Run initial indexing if requested
    indexed_nodes = 0
    indexed_edges = 0
    quality_rules_loaded = 0

    if not no_index:
        indexed_nodes, indexed_edges, quality_rules_loaded = _run_initial_indexing(db_path, root)
    else:
        from vibetrace.quality.rules import QUALITY_RULES_CATALOG

        quality_rules_loaded = len(QUALITY_RULES_CATALOG)

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
        "quality_rules_loaded": quality_rules_loaded,
        "has_containers": has_containers,
        "trivy_available": trivy_available,
        "semgrep_available": semgrep_available,
    }
