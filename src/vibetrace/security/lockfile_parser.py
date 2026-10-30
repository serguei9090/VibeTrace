"""Lockfile parsers for extracting dependency packages and versions across ecosystems."""

from __future__ import annotations

import json
import re
import tomllib
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class DependencyPackage:
    name: str
    version: str
    ecosystem: str  # PyPI | npm | Go | Cargo | Maven
    lockfile_path: str


class LockfileParser:
    """Detects and parses package lockfiles to extract (name, version, ecosystem)."""

    @classmethod
    def parse_all_lockfiles(cls, project_root: Path) -> list[DependencyPackage]:
        """Discovers and parses all supported lockfiles in the project repository."""
        packages: list[DependencyPackage] = []

        # 1. uv.lock (PyPI)
        for uv_path in project_root.rglob("uv.lock"):
            if ".venv" in uv_path.parts or "node_modules" in uv_path.parts:
                continue
            packages.extend(cls.parse_uv_lock(uv_path, project_root))

        # 2. bun.lock / package-lock.json (npm)
        for bun_path in project_root.rglob("bun.lock"):
            if ".venv" in bun_path.parts or "node_modules" in bun_path.parts:
                continue
            packages.extend(cls.parse_bun_lock(bun_path, project_root))

        for npm_path in project_root.rglob("package-lock.json"):
            if ".venv" in npm_path.parts or "node_modules" in npm_path.parts:
                continue
            packages.extend(cls.parse_package_lock_json(npm_path, project_root))

        # 3. Cargo.lock (Cargo)
        for cargo_path in project_root.rglob("Cargo.lock"):
            if ".venv" in cargo_path.parts or "target" in cargo_path.parts:
                continue
            packages.extend(cls.parse_cargo_lock(cargo_path, project_root))

        # 4. go.sum (Go)
        for go_path in project_root.rglob("go.sum"):
            if ".venv" in go_path.parts:
                continue
            packages.extend(cls.parse_go_sum(go_path, project_root))

        return packages

    @classmethod
    def parse_uv_lock(cls, file_path: Path, project_root: Path) -> list[DependencyPackage]:
        """Parses uv.lock TOML file."""
        try:
            raw = tomllib.loads(file_path.read_text(encoding="utf-8"))
        except Exception:
            return []

        rel_path = str(file_path.relative_to(project_root)).replace("\\", "/")
        pkgs: list[DependencyPackage] = []
        for pkg in raw.get("package", []):
            name = pkg.get("name")
            version = pkg.get("version")
            if name and version:
                pkgs.append(
                    DependencyPackage(
                        name=name,
                        version=version,
                        ecosystem="PyPI",
                        lockfile_path=rel_path,
                    )
                )
        return pkgs

    @classmethod
    def parse_bun_lock(cls, file_path: Path, project_root: Path) -> list[DependencyPackage]:
        """Parses modern bun.lock JSON format."""
        try:
            raw = json.loads(file_path.read_text(encoding="utf-8"))
        except Exception:
            return []

        rel_path = str(file_path.relative_to(project_root)).replace("\\", "/")
        pkgs: list[DependencyPackage] = []
        packages_dict = raw.get("packages", {})
        for _key, details in packages_dict.items():
            if isinstance(details, list) and len(details) > 0:
                spec = details[0]  # e.g. "@babel/core@7.29.7" or "axios@1.13.4"
                if "@" in spec:
                    last_at = spec.rfind("@")
                    name = spec[:last_at]
                    version = spec[last_at + 1 :]
                    if name and version and not version.startswith(("^", "~", "*")):
                        pkgs.append(
                            DependencyPackage(
                                name=name,
                                version=version,
                                ecosystem="npm",
                                lockfile_path=rel_path,
                            )
                        )
        return pkgs

    @classmethod
    def parse_package_lock_json(
        cls, file_path: Path, project_root: Path
    ) -> list[DependencyPackage]:
        """Parses standard npm package-lock.json."""
        try:
            raw = json.loads(file_path.read_text(encoding="utf-8"))
        except Exception:
            return []

        rel_path = str(file_path.relative_to(project_root)).replace("\\", "/")
        pkgs: list[DependencyPackage] = []
        packages_dict = raw.get("packages", {})
        for pkg_key, details in packages_dict.items():
            if not pkg_key:
                continue
            name = details.get("name") or pkg_key.split("node_modules/")[-1]
            version = details.get("version")
            if name and version:
                pkgs.append(
                    DependencyPackage(
                        name=name,
                        version=version,
                        ecosystem="npm",
                        lockfile_path=rel_path,
                    )
                )
        return pkgs

    @classmethod
    def parse_cargo_lock(cls, file_path: Path, project_root: Path) -> list[DependencyPackage]:
        """Parses Cargo.lock TOML."""
        try:
            raw = tomllib.loads(file_path.read_text(encoding="utf-8"))
        except Exception:
            return []

        rel_path = str(file_path.relative_to(project_root)).replace("\\", "/")
        pkgs: list[DependencyPackage] = []
        for pkg in raw.get("package", []):
            name = pkg.get("name")
            version = pkg.get("version")
            if name and version:
                pkgs.append(
                    DependencyPackage(
                        name=name,
                        version=version,
                        ecosystem="crates.io",
                        lockfile_path=rel_path,
                    )
                )
        return pkgs

    @classmethod
    def parse_go_sum(cls, file_path: Path, project_root: Path) -> list[DependencyPackage]:
        """Parses go.sum lines."""
        try:
            content = file_path.read_text(encoding="utf-8")
        except Exception:
            return []

        rel_path = str(file_path.relative_to(project_root)).replace("\\", "/")
        seen: set[tuple[str, str]] = set()
        pkgs: list[DependencyPackage] = []

        for line in content.splitlines():
            parts = line.strip().split()
            if len(parts) >= 2:
                name = parts[0]
                version_raw = parts[1].split("/")[0]  # strip /go.mod
                clean_ver = re.sub(r"^v", "", version_raw)
                if (name, clean_ver) not in seen:
                    seen.add((name, clean_ver))
                    pkgs.append(
                        DependencyPackage(
                            name=name,
                            version=clean_ver,
                            ecosystem="Go",
                            lockfile_path=rel_path,
                        )
                    )
        return pkgs
