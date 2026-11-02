"""CLI subcommands for analyzing transitive dependencies, causation paths, and diamond conflicts."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from vibetrace.security.dep_graph import (
    DependencyGraphBuilder,
    normalize_package_name,
)
from vibetrace.services.graph_service import safe_str


def cmd_deps(args: argparse.Namespace) -> int:
    """Dispatches `vibetrace deps` subcommands."""
    deps_action = getattr(args, "deps_action", None)
    if deps_action == "why":
        return _cmd_deps_why(args)
    elif deps_action == "tree":
        return _cmd_deps_tree(args)
    elif deps_action == "conflicts":
        return _cmd_deps_conflicts(args)
    print("Error: Unknown deps action. Use 'why', 'tree', or 'conflicts'.", file=sys.stderr)
    return 1


def _cmd_deps_why(args: argparse.Namespace) -> int:
    """Explains why a package is installed by locating all root causation chains."""
    path_hint = getattr(args, "path", ".")
    project_root = Path(path_hint).resolve()
    target_package = getattr(args, "package", "")

    if not target_package:
        print("Error: package argument is required for 'deps why'.", file=sys.stderr)
        return 1

    graph = DependencyGraphBuilder.build(project_root)
    norm_target = normalize_package_name(target_package)
    chains = graph.resolve_why(norm_target)

    if getattr(args, "json", False):
        data = {
            "target": target_package,
            "normalized": norm_target,
            "found": len(chains) > 0,
            "chains_count": len(chains),
            "chains": [c.model_dump() for c in chains],
        }
        print(json.dumps(data, indent=2))
        return 0 if chains else 1

    if not chains:
        print(f"❌ Package '{target_package}' was not found in project lockfiles or manifests.")
        return 1

    rendered = graph.render_ascii_chains(target_package, chains)
    print(safe_str(rendered))
    return 0


def _cmd_deps_tree(args: argparse.Namespace) -> int:
    """Renders top-down dependency tree from root manifest dependencies."""
    path_hint = getattr(args, "path", ".")
    project_root = Path(path_hint).resolve()
    target_filter = getattr(args, "target", None)
    max_depth = getattr(args, "depth", 4)

    graph = DependencyGraphBuilder.build(project_root)

    if target_filter:
        norm_target = normalize_package_name(target_filter)
        chains = graph.resolve_why(norm_target)
        if getattr(args, "json", False):
            print(json.dumps([c.model_dump() for c in chains], indent=2))
            return 0 if chains else 1
        rendered = graph.render_ascii_chains(target_filter, chains)
        print(safe_str(rendered))
        return 0 if chains else 1

    # Render entire direct roots tree
    if getattr(args, "json", False):
        summary = {
            "total_packages": sum(len(nodes) for nodes in graph.nodes.values()),
            "direct_roots": sorted(graph.direct_roots),
            "root_dependencies": {
                root: sorted(graph.adj_down.get(root, set())) for root in sorted(graph.direct_roots)
            },
        }
        print(json.dumps(summary, indent=2))
        return 0

    print("## 🌳 Project Dependency Tree (Direct Manifest Roots)\n")
    if not graph.direct_roots:
        print(
            "No direct manifest dependencies detected in "
            "pyproject.toml, package.json, or Cargo.toml."
        )
        return 0

    def _print_children(parent: str, current_depth: int, branch_visited: set[str]) -> None:
        if current_depth > max_depth:
            return
        children = sorted(graph.adj_down.get(parent, set()))
        for child in children:
            indent = "   " * current_depth
            child_node = graph.nodes.get(child, [None])[0]
            child_ver = f"@{child_node.version}" if child_node else ""
            cycle_note = " 🔄 (cycle)" if child in branch_visited else ""
            print(f"{indent}└── {child}{child_ver}{cycle_note}")
            if child not in branch_visited:
                _print_children(child, current_depth + 1, branch_visited | {child})

    for root in sorted(graph.direct_roots):
        node = graph.nodes.get(root, [None])[0]
        ver = f"@{node.version}" if node else ""
        manifest = node.direct_manifest if node else "manifest"
        print(f"📦 {root}{ver} ({manifest})")
        _print_children(root, 1, {root})
        print()

    return 0


def _cmd_deps_conflicts(args: argparse.Namespace) -> int:
    """Audits lockfiles for diamond version conflicts and multiple resolved versions."""
    path_hint = getattr(args, "path", ".")
    project_root = Path(path_hint).resolve()

    graph = DependencyGraphBuilder.build(project_root)
    conflicts = graph.detect_conflicts()

    if getattr(args, "json", False):
        data = {
            "has_conflicts": len(conflicts) > 0,
            "conflicts_count": len(conflicts),
            "conflicts": [c.model_dump() for c in conflicts],
        }
        print(json.dumps(data, indent=2))
        return 1 if conflicts else 0

    print("## 💎 Diamond Dependency Version Conflict Audit\n")
    if not conflicts:
        print("✅ No diamond version conflicts detected across lockfiles.")
        return 0

    print(f"⚠️ Found {len(conflicts)} diamond dependency collision(s):\n")
    for c in conflicts:
        print(f"### Collision: `{c.package_name}` ({c.ecosystem})")
        print(f"- **Multiple Versions Resolved:** {', '.join(c.versions)}")
        print("- **Parent Requirers by Version:**")
        for v, parents in c.parents_by_version.items():
            parent_list = ", ".join(parents) if parents else "(direct/unknown)"
            print(f"  - `v{v}` required by: `{parent_list}`")
        print()

    return 1
