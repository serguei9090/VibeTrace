"""CLI commands for workspace configuration and sub-engine preset management."""

from __future__ import annotations

import argparse
from pathlib import Path

from vibetrace.config import get_config_path, load_config, resolve_linter_mode, save_linter_mode
from vibetrace.quality.adapters.biome import BiomeAdapter
from vibetrace.quality.adapters.golang_lint import GolangLintAdapter
from vibetrace.quality.adapters.python_ruff import RuffAdapter
from vibetrace.quality.adapters.rust_clippy import CargoClippyAdapter
from vibetrace.quality.adapters.typescript_tsc import TypeScriptAdapter


def cmd_config_quality(args: argparse.Namespace) -> int:
    """Displays or updates quality sub-engine presets in .vibetrace.toml."""
    path_hint = getattr(args, "path", ".")
    project_root = Path(path_hint).resolve()
    if project_root.is_file():
        project_root = project_root.parent

    set_opt = getattr(args, "set_opt", None)
    if set_opt:
        if "=" not in set_opt:
            print("[ERROR] Format must be KEY=VALUE, e.g. --set ruff=strict or --set biome=project")
            return 1
        key, val = set_opt.split("=", 1)
        key = key.strip().lower()
        val = val.strip().lower()
        if key == "tsc":
            key = "typescript"
        elif key in ("go", "golangci-lint"):
            key = "golang"
        elif key in ("clippy", "cargo-clippy"):
            key = "rust"

        supported_keys = ("ruff", "biome", "typescript", "golang", "rust")
        if key not in supported_keys:
            print(f"[ERROR] Unknown sub-engine '{key}'. Supported: {', '.join(supported_keys)}")
            return 1
        if val not in ("auto", "project", "strict"):
            print(f"[ERROR] Invalid mode '{val}'. Supported: auto, project, strict")
            return 1

        save_linter_mode(project_root, key, val)
        target_cfg = get_config_path(project_root) or (project_root / ".vibetrace.toml")
        try:
            rel_cfg = target_cfg.relative_to(project_root).as_posix()
        except ValueError:
            rel_cfg = str(target_cfg)
        print(f"[CONFIG] Updated {key} mode to '{val}' in {rel_cfg}.")
        return 0

    cfg = load_config(project_root)
    engines = [
        ("ruff", "Ruff (Python)", RuffAdapter),
        ("biome", "Biome (JS/TS/JSON/CSS)", BiomeAdapter),
        ("typescript", "TypeScript (tsc)", TypeScriptAdapter),
        ("golang", "Go (golangci-lint)", GolangLintAdapter),
        ("rust", "Rust (cargo clippy)", CargoClippyAdapter),
    ]

    print("# ⚙️ VibeTrace Quality Sub-Engine Configurations\n")
    print("| Sub-Engine | Configured Mode | Resolved Mode | Active Config Target |")
    print("| :--- | :--- | :--- | :--- |")
    for key, label, adapter_cls in engines:
        configured = getattr(cfg.quality.linter_configs, key, "auto")
        resolved = resolve_linter_mode(project_root, key)
        adapter = adapter_cls()
        cfg_path = adapter._resolve_config_path(project_root)
        if cfg_path:
            try:
                rel_cfg = cfg_path.relative_to(project_root).as_posix()
            except ValueError:
                rel_cfg = f"[Preset] {cfg_path.name}"
        else:
            rel_cfg = "[Not found / Auto]"
        print(f"| **{label}** | `{configured}` | **{resolved}** | `{rel_cfg}` |")

    print("\nTo switch an engine preset, run:")
    print("  `vibetrace config quality --set <engine>=<project|strict|auto>`")
    return 0


def register_config_parser(sub: argparse._SubParsersAction) -> None:
    """Registers 'config' top-level command and quality switcher."""
    p_config = sub.add_parser(
        "config",
        help="Inspect or configure workspace settings and sub-engine modes",
        description="Inspect or update project configuration in .vibetrace.toml.",
    )
    cfg_sub = p_config.add_subparsers(dest="config_subcommand")

    p_quality = cfg_sub.add_parser(
        "quality",
        help="Inspect or switch quality sub-engine presets ('project' vs 'strict')",
        description=(
            "Inspect active linter configurations for Ruff, Biome, and TypeScript (tsc), "
            "or switch between workspace-native configs and VibeTrace strict presets."
        ),
    )
    p_quality.add_argument(
        "--set",
        dest="set_opt",
        metavar="KEY=VALUE",
        help="Update engine preset, e.g. '--set ruff=strict' or '--set biome=project'",
    )
    p_quality.add_argument(
        "--path",
        default=".",
        help="Project root directory path (default: current directory)",
    )
    p_quality.set_defaults(func=cmd_config_quality)
    p_config.set_defaults(func=lambda args: p_config.print_help() or 0)
