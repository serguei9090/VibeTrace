"""Lockfile parsers for extracting dependency packages and versions across ecosystems."""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from pathlib import Path

import tomllib


@dataclass(frozen=True)
class DependencyPackage:
    name: str
    version: str
    ecosystem: str  # PyPI | npm | Go | crates.io | Maven
    lockfile_path: str


_LOCKFILE_HASH_CACHE: dict[str, tuple[str, list[DependencyPackage]]] = {}


class LockfileParser:
    """Detects and parses package lockfiles to extract (name, version, ecosystem)."""

    @classmethod
    def _cached_parse(cls, file_path: Path, fn) -> list[DependencyPackage]:
        try:
            digest = hashlib.sha256(file_path.read_bytes()).hexdigest()
        except OSError:
            return []
        key = str(file_path)
        cached = _LOCKFILE_HASH_CACHE.get(key)
        if cached and cached[0] == digest:
            return cached[1]
        pkgs = fn(file_path)
        _LOCKFILE_HASH_CACHE[key] = (digest, pkgs)
        return pkgs

    @classmethod
    def parse_all_lockfiles(cls, project_root: Path) -> list[DependencyPackage]:
        """Discovers and parses all supported lockfiles in the project repository."""
        packages: list[DependencyPackage] = []

        # 1. uv.lock / poetry.lock / requirements*.txt (PyPI)
        for uv_path in (*project_root.rglob("uv.lock"), *project_root.rglob("poetry.lock")):
            if ".venv" in uv_path.parts or "node_modules" in uv_path.parts:
                continue
            packages.extend(
                cls._cached_parse(uv_path, lambda p: cls.parse_uv_lock(p, project_root))
            )

        for req_path in project_root.rglob("requirements*.txt"):
            if ".venv" in req_path.parts or "node_modules" in req_path.parts:
                continue
            packages.extend(
                cls._cached_parse(req_path, lambda p: cls.parse_requirements_txt(p, project_root))
            )

        # 2. bun.lock / package-lock.json (npm)
        for bun_path in project_root.rglob("bun.lock"):
            if ".venv" in bun_path.parts or "node_modules" in bun_path.parts:
                continue
            packages.extend(
                cls._cached_parse(bun_path, lambda p: cls.parse_bun_lock(p, project_root))
            )

        for npm_path in project_root.rglob("package-lock.json"):
            if ".venv" in npm_path.parts or "node_modules" in npm_path.parts:
                continue
            packages.extend(
                cls._cached_parse(npm_path, lambda p: cls.parse_package_lock_json(p, project_root))
            )

        # 3. Cargo.lock (Cargo)
        for cargo_path in project_root.rglob("Cargo.lock"):
            if ".venv" in cargo_path.parts or "target" in cargo_path.parts:
                continue
            packages.extend(
                cls._cached_parse(cargo_path, lambda p: cls.parse_cargo_lock(p, project_root))
            )

        # 4. go.sum (Go)
        for go_path in project_root.rglob("go.sum"):
            if ".venv" in go_path.parts:
                continue
            packages.extend(cls._cached_parse(go_path, lambda p: cls.parse_go_sum(p, project_root)))

        return packages

    @classmethod
    def parse_uv_lock(cls, file_path: Path, project_root: Path) -> list[DependencyPackage]:
        """Parses uv.lock and poetry.lock TOML files."""
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
    def parse_requirements_txt(cls, file_path: Path, project_root: Path) -> list[DependencyPackage]:
        """Parses pinned `pkg==version` entries from requirements.txt files."""
        try:
            content = file_path.read_text(encoding="utf-8")
        except OSError:
            return []
        rel_path = str(file_path.relative_to(project_root)).replace("\\", "/")
        pkgs: list[DependencyPackage] = []
        for raw_line in content.splitlines():
            line = raw_line.split("#", 1)[0].strip()
            m = re.match(r"^([A-Za-z0-9_.-]+)==([A-Za-z0-9_.+-]+)", line)
            if m:
                pkgs.append(
                    DependencyPackage(
                        name=m.group(1),
                        version=m.group(2),
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
                spec = details[0]
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
                version_raw = parts[1].split("/")[0]
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
