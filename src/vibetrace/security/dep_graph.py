"""Transitive Dependency Graph Resolution & Root-Cause Visualizer.

Parses project lockfiles and manifests into a Directed Acyclic Graph (DAG) to
resolve inverted causation chains, detect diamond version conflicts, and explain
why transitive packages are installed.
"""

from __future__ import annotations

import json
import re
import tomllib
from collections import deque
from pathlib import Path

from pydantic import BaseModel, Field


def normalize_package_name(name: str, ecosystem: str = "PyPI") -> str:
    """Normalize package names according to ecosystem conventions."""
    clean = name.strip()
    if ecosystem.lower() in ("pypi", "python"):
        # PEP 503 normalization: lowercase and replace underscores/periods with dashes
        return re.sub(r"[-_.]+", "-", clean).lower()
    return clean.lower()


class PackageNode(BaseModel):
    """Represents a discrete resolved package in a project's dependency graph."""

    name: str = Field(description="Normalized package name")
    display_name: str = Field(description="Original case-preserved package name")
    version: str = Field(description="Resolved package version")
    ecosystem: str = Field(description="Ecosystem: PyPI | npm | Cargo | Go")
    lockfile_path: str = Field(description="Relative path to the lockfile declaring this package")
    is_direct: bool = Field(default=False, description="True if declared in root project manifest")
    direct_manifest: str | None = Field(
        default=None, description="Manifest path (e.g. pyproject.toml) if direct"
    )


class DependencyChain(BaseModel):
    """Represents an inverted causation path from a direct manifest package to a target."""

    path: list[str] = Field(description="Top-down sequence of package names from root to target")
    root_package: str = Field(
        description="The direct manifest dependency at the root of this chain"
    )
    target_package: str = Field(description="The target dependency being investigated")
    depth: int = Field(description="Number of dependency hops from direct root to target")


class DiamondConflict(BaseModel):
    """Represents a diamond version collision where dependencies require different versions."""

    package_name: str = Field(description="Name of the conflicting dependency")
    ecosystem: str = Field(description="Package ecosystem")
    versions: list[str] = Field(description="Distinct versions resolved across lockfiles")
    parents_by_version: dict[str, list[str]] = Field(
        default_factory=dict,
        description="Map of version string to list of parent packages requiring it",
    )


class DependencyGraphReport(BaseModel):
    """Comprehensive dependency audit report."""

    target_package: str | None = None
    chains: list[DependencyChain] = Field(default_factory=list)
    conflicts: list[DiamondConflict] = Field(default_factory=list)
    total_packages: int = 0
    direct_packages: int = 0
    transitive_packages: int = 0


class DependencyGraph:
    """Directed dependency graph supporting inverted path resolution and conflict analysis."""

    def __init__(self) -> None:
        self.nodes: dict[str, list[PackageNode]] = {}
        self.adj_down: dict[str, set[str]] = {}
        self.adj_up: dict[str, set[str]] = {}
        self.direct_roots: set[str] = set()

    def add_node(self, node: PackageNode) -> None:
        """Adds or updates a package node in the graph."""
        key = node.name
        if key not in self.nodes:
            self.nodes[key] = []
        # Update or append
        existing = next((n for n in self.nodes[key] if n.version == node.version), None)
        if existing:
            if node.is_direct and not existing.is_direct:
                existing.is_direct = True
                existing.direct_manifest = node.direct_manifest
        else:
            self.nodes[key].append(node)

        if node.is_direct:
            self.direct_roots.add(key)

        if key not in self.adj_down:
            self.adj_down[key] = set()
        if key not in self.adj_up:
            self.adj_up[key] = set()

    def add_edge(self, parent_name: str, child_name: str) -> None:
        """Adds a directed dependency edge parent -> child."""
        p = parent_name.strip()
        c = child_name.strip()
        if not p or not c:
            return

        if p not in self.adj_down:
            self.adj_down[p] = set()
        self.adj_down[p].add(c)

        if c not in self.adj_up:
            self.adj_up[c] = set()
        self.adj_up[c].add(p)

    def mark_direct_roots(self, direct_map: dict[str, str]) -> None:
        """Marks packages as direct manifest dependencies (name -> manifest_file)."""
        for pkg_name, manifest in direct_map.items():
            norm = pkg_name.strip()
            self.direct_roots.add(norm)
            if norm in self.nodes:
                for n in self.nodes[norm]:
                    n.is_direct = True
                    n.direct_manifest = manifest

    def resolve_why(self, target_name: str) -> list[DependencyChain]:
        """Performs inverted BFS to find all root-cause paths from direct dependencies to target."""
        norm_target = target_name.strip()
        matching_keys = [
            k for k in self.nodes if k == norm_target or k.lower() == norm_target.lower()
        ]
        if not matching_keys:
            # Try fuzzy suffix or exact match in adjacency
            matching_keys = [
                k for k in self.adj_up if k == norm_target or k.lower() == norm_target.lower()
            ]

        if not matching_keys:
            return []

        key = matching_keys[0]

        # If target itself is a direct dependency
        if key in self.direct_roots:
            return [
                DependencyChain(
                    path=[key],
                    root_package=key,
                    target_package=key,
                    depth=0,
                )
            ]

        chains: list[DependencyChain] = []
        # BFS queue elements: (current_node, [path_reversed])
        queue: deque[tuple[str, list[str]]] = deque([(key, [key])])
        visited_states: set[tuple[str, str]] = set()

        while queue:
            curr, path_rev = queue.popleft()

            if curr in self.direct_roots:
                full_path = list(reversed(path_rev))
                chain = DependencyChain(
                    path=full_path,
                    root_package=curr,
                    target_package=key,
                    depth=len(full_path) - 1,
                )
                if chain not in chains:
                    chains.append(chain)
                continue

            parents = self.adj_up.get(curr, set())
            for parent in sorted(parents):
                if parent in path_rev:
                    # Prevent circular dependency loops
                    continue
                state = (curr, parent)
                if state in visited_states:
                    continue
                visited_states.add(state)
                queue.append((parent, [*path_rev, parent]))

        # Sort shortest chain first
        chains.sort(key=lambda c: (c.depth, c.root_package))
        return chains

    def detect_conflicts(self) -> list[DiamondConflict]:
        """Detects diamond version conflicts where a package has multiple resolved versions."""
        conflicts: list[DiamondConflict] = []
        for name, nodes in sorted(self.nodes.items()):
            distinct_versions = sorted({n.version for n in nodes if n.version})
            if len(distinct_versions) > 1:
                parents_map: dict[str, list[str]] = {}
                for v in distinct_versions:
                    parents_map[v] = sorted(self.adj_up.get(name, set()))
                conflicts.append(
                    DiamondConflict(
                        package_name=name,
                        ecosystem=nodes[0].ecosystem if nodes else "unknown",
                        versions=distinct_versions,
                        parents_by_version=parents_map,
                    )
                )
        return conflicts

    def render_ascii_chains(self, target_name: str, chains: list[DependencyChain]) -> str:
        """Renders an ASCII causation tree showing how direct packages pull in the target."""
        if not chains:
            return f"❌ Package '{target_name}' is not in the dependency graph."

        lines: list[str] = [
            f"## 🌳 Transitive Causation Tree for '{target_name}'",
            f"Found {len(chains)} dependency path(s) originating from root manifest(s):\n",
        ]

        for i, chain in enumerate(chains, 1):
            lines.append(f"### Path #{i} (depth: {chain.depth})")
            for depth, pkg in enumerate(chain.path):
                indent = "    " * depth
                branch = "└── " if depth > 0 else ""
                node_info = self.nodes.get(pkg, [None])[0]
                version_str = f"@{node_info.version}" if node_info else ""
                tag = ""
                if depth == 0:
                    manifest = node_info.direct_manifest if node_info else "manifest"
                    tag = f" 📌 [DIRECT: {manifest or 'project'}]"
                elif depth == len(chain.path) - 1:
                    tag = " 🚨 [TARGET]"
                lines.append(f"{indent}{branch}{pkg}{version_str}{tag}")
            lines.append("")

        return "\n".join(lines).rstrip()


class DependencyGraphBuilder:
    """Builds a unified DependencyGraph across project lockfiles and manifests."""

    @classmethod
    def build(cls, project_root: Path) -> DependencyGraph:
        """Parses manifests and lockfiles to construct the full project dependency graph."""
        graph = DependencyGraph()
        direct_map = cls._extract_direct_dependencies(project_root)

        # Parse PyPI lockfiles
        for uv_path in (*project_root.rglob("uv.lock"), *project_root.rglob("poetry.lock")):
            if cls._should_skip(uv_path):
                continue
            cls._parse_uv_or_poetry_lock(uv_path, project_root, graph)

        # Parse npm lockfiles
        for npm_path in project_root.rglob("package-lock.json"):
            if cls._should_skip(npm_path):
                continue
            cls._parse_package_lock(npm_path, project_root, graph)

        for bun_path in project_root.rglob("bun.lock"):
            if cls._should_skip(bun_path):
                continue
            cls._parse_bun_lock(bun_path, project_root, graph)

        # Parse Cargo lockfiles
        for cargo_path in project_root.rglob("Cargo.lock"):
            if cls._should_skip(cargo_path):
                continue
            cls._parse_cargo_lock(cargo_path, project_root, graph)

        # Apply direct roots
        graph.mark_direct_roots(direct_map)
        return graph

    @staticmethod
    def _should_skip(path: Path) -> bool:
        parts = path.parts
        return ".venv" in parts or "node_modules" in parts or "target" in parts or ".git" in parts

    @classmethod
    def _extract_direct_dependencies(cls, project_root: Path) -> dict[str, str]:
        """Extracts direct dependencies from pyproject.toml, package.json, Cargo.toml."""
        direct: dict[str, str] = {}

        # 1. pyproject.toml
        pyproject_file = project_root / "pyproject.toml"
        if pyproject_file.is_file():
            try:
                data = tomllib.loads(pyproject_file.read_text(encoding="utf-8"))
                project = data.get("project", {})
                for dep in project.get("dependencies", []):
                    name = cls._parse_pep508_name(dep)
                    if name:
                        direct[normalize_package_name(name, "PyPI")] = "pyproject.toml"

                # optional dependencies
                opt_deps = project.get("optional-dependencies", {})
                for group in opt_deps.values():
                    for dep in group:
                        name = cls._parse_pep508_name(dep)
                        if name:
                            direct[normalize_package_name(name, "PyPI")] = "pyproject.toml"

                # uv dependency-groups
                groups = data.get("dependency-groups", {})
                for group in groups.values():
                    for dep in group:
                        name = cls._parse_pep508_name(dep)
                        if name:
                            direct[normalize_package_name(name, "PyPI")] = "pyproject.toml"

                # poetry
                tool_poetry = data.get("tool", {}).get("poetry", {}).get("dependencies", {})
                for dep_name in tool_poetry:
                    if dep_name.lower() != "python":
                        direct[normalize_package_name(dep_name, "PyPI")] = "pyproject.toml"
            except Exception:
                pass

        # 2. package.json
        pkg_json = project_root / "package.json"
        if pkg_json.is_file():
            try:
                data = json.loads(pkg_json.read_text(encoding="utf-8"))
                for section in ("dependencies", "devDependencies", "peerDependencies"):
                    for dep_name in data.get(section, {}):
                        direct[normalize_package_name(dep_name, "npm")] = "package.json"
            except Exception:
                pass

        # 3. Cargo.toml
        cargo_toml = project_root / "Cargo.toml"
        if cargo_toml.is_file():
            try:
                data = tomllib.loads(cargo_toml.read_text(encoding="utf-8"))
                for section in ("dependencies", "dev-dependencies", "build-dependencies"):
                    for dep_name in data.get(section, {}):
                        direct[normalize_package_name(dep_name, "Cargo")] = "Cargo.toml"
            except Exception:
                pass

        return direct

    @staticmethod
    def _parse_pep508_name(dep_spec: str) -> str | None:
        """Extracts the package name part from a PEP 508 dependency string."""
        m = re.match(r"^([A-Za-z0-9_\-\.]+)", dep_spec.strip())
        return m.group(1) if m else None

    @classmethod
    def _parse_uv_or_poetry_lock(
        cls, lock_file: Path, project_root: Path, graph: DependencyGraph
    ) -> None:
        """Parses uv.lock and poetry.lock."""
        try:
            data = tomllib.loads(lock_file.read_text(encoding="utf-8"))
        except Exception:
            return

        rel_path = str(lock_file.relative_to(project_root)).replace("\\", "/")
        packages = data.get("package", [])

        for pkg in packages:
            raw_name = pkg.get("name")
            version = pkg.get("version", "0.0.0")
            if not raw_name:
                continue

            name = normalize_package_name(raw_name, "PyPI")
            node = PackageNode(
                name=name,
                display_name=raw_name,
                version=version,
                ecosystem="PyPI",
                lockfile_path=rel_path,
            )
            graph.add_node(node)

            # uv dependencies list: [{ name = "..." }, ...] or strings
            raw_deps = pkg.get("dependencies", [])
            for dep in raw_deps:
                dep_name: str | None = None
                if isinstance(dep, dict):
                    dep_name = dep.get("name")
                elif isinstance(dep, str):
                    dep_name = cls._parse_pep508_name(dep)

                if dep_name:
                    norm_dep = normalize_package_name(dep_name, "PyPI")
                    graph.add_edge(name, norm_dep)

            # poetry dependencies dict: { "dep_name": "..." }
            if isinstance(raw_deps, dict):
                for dep_key in raw_deps:
                    if isinstance(dep_key, str) and dep_key.lower() != "python":
                        norm_dep = normalize_package_name(dep_key, "PyPI")
                        graph.add_edge(name, norm_dep)

    @classmethod
    def _parse_package_lock(
        cls, lock_file: Path, project_root: Path, graph: DependencyGraph
    ) -> None:
        """Parses npm package-lock.json (v2 and v3 format)."""
        try:
            data = json.loads(lock_file.read_text(encoding="utf-8"))
        except Exception:
            return

        rel_path = str(lock_file.relative_to(project_root)).replace("\\", "/")
        packages = data.get("packages", {})

        for pkg_path, info in packages.items():
            if not pkg_path:  # Root package ""
                continue
            name_part = pkg_path.split("node_modules/")[-1]
            version = info.get("version", "0.0.0")
            norm_name = normalize_package_name(name_part, "npm")

            node = PackageNode(
                name=norm_name,
                display_name=name_part,
                version=version,
                ecosystem="npm",
                lockfile_path=rel_path,
            )
            graph.add_node(node)

            for dep_name in info.get("dependencies", {}):
                norm_dep = normalize_package_name(dep_name, "npm")
                graph.add_edge(norm_name, norm_dep)

    @classmethod
    def _parse_bun_lock(cls, lock_file: Path, project_root: Path, graph: DependencyGraph) -> None:
        """Parses bun.lock (JSON format)."""
        try:
            data = json.loads(lock_file.read_text(encoding="utf-8"))
        except Exception:
            return

        rel_path = str(lock_file.relative_to(project_root)).replace("\\", "/")
        packages = data.get("packages", {})
        for name_part, info in packages.items():
            if not name_part:
                continue
            version = info.get("version", "0.0.0")
            norm_name = normalize_package_name(name_part, "npm")
            node = PackageNode(
                name=norm_name,
                display_name=name_part,
                version=version,
                ecosystem="npm",
                lockfile_path=rel_path,
            )
            graph.add_node(node)
            for dep_name in info.get("dependencies", {}):
                norm_dep = normalize_package_name(dep_name, "npm")
                graph.add_edge(norm_name, norm_dep)

    @classmethod
    def _parse_cargo_lock(cls, lock_file: Path, project_root: Path, graph: DependencyGraph) -> None:
        """Parses Cargo.lock."""
        try:
            data = tomllib.loads(lock_file.read_text(encoding="utf-8"))
        except Exception:
            return

        rel_path = str(lock_file.relative_to(project_root)).replace("\\", "/")
        packages = data.get("package", [])

        for pkg in packages:
            raw_name = pkg.get("name")
            version = pkg.get("version", "0.0.0")
            if not raw_name:
                continue

            name = normalize_package_name(raw_name, "Cargo")
            node = PackageNode(
                name=name,
                display_name=raw_name,
                version=version,
                ecosystem="Cargo",
                lockfile_path=rel_path,
            )
            graph.add_node(node)

            for dep_entry in pkg.get("dependencies", []):
                # Format: "dep_name" or "dep_name 1.2.3 (registry+...)"
                dep_name = dep_entry.split()[0]
                norm_dep = normalize_package_name(dep_name, "Cargo")
                graph.add_edge(name, norm_dep)
