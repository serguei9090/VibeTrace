import logging
import os
from pathlib import Path

logger = logging.getLogger(__name__)


class ModuleResolver:
    """Resolves language-specific module names to local filesystem paths."""

    SUPPORTED_EXTENSIONS = (
        ".py",
        ".js",
        ".jsx",
        ".ts",
        ".tsx",
        ".go",
        ".rs",
        ".java",
        ".c",
        ".h",
        ".cpp",
        ".cc",
        ".cxx",
        ".hpp",
        ".cs",
        ".rb",
        ".php",
    )

    def __init__(self, project_root: Path):
        self.project_root = project_root.resolve()
        self.module_map: dict[str, str] = {}
        self.known_files: set[str] = set()
        self.go_modules: dict[str, str] = self._detect_go_modules()
        self.cargo_packages: dict[str, str] = self._detect_cargo_packages()
        self.go_module: str | None = next(iter(self.go_modules.keys()), None)
        self.cargo_package: str | None = next(iter(self.cargo_packages.keys()), None)
        self._build_map()

    def _detect_go_modules(self) -> dict[str, str]:
        """Parse module names from root and subproject go.mod files."""
        modules: dict[str, str] = {}
        for root, dirs, files in os.walk(self.project_root):
            dirs[:] = [
                d
                for d in dirs
                if not d.startswith(".") and d not in ("vendor", "node_modules", ".venv")
            ]
            if "go.mod" in files:
                g_path = Path(root) / "go.mod"
                try:
                    content = g_path.read_text(encoding="utf-8", errors="replace")
                    for line in content.splitlines():
                        stripped = line.strip()
                        if stripped.startswith("module "):
                            parts = stripped.split()
                            if len(parts) >= 2:
                                mod_name = parts[1].strip("\"'")
                                rel = str(Path(root).relative_to(self.project_root)).replace(
                                    "\\", "/"
                                )
                                modules[mod_name] = "" if rel == "." else rel
                                break
                except Exception as e:
                    logger.debug("Failed to parse go.mod at %s: %s", g_path, e)
        return modules

    def _detect_cargo_packages(self) -> dict[str, str]:
        """Parse package names from root and subproject Cargo.toml files."""
        packages: dict[str, str] = {}
        for root, dirs, files in os.walk(self.project_root):
            dirs[:] = [
                d
                for d in dirs
                if not d.startswith(".") and d not in ("target", "node_modules", ".venv")
            ]
            if "Cargo.toml" in files:
                c_path = Path(root) / "Cargo.toml"
                try:
                    content = c_path.read_text(encoding="utf-8", errors="replace")
                    in_package = False
                    for line in content.splitlines():
                        stripped = line.strip()
                        if stripped.startswith("[") and stripped.endswith("]"):
                            in_package = stripped == "[package]"
                        elif in_package and stripped.startswith("name"):
                            parts = stripped.split("=", 1)
                            if len(parts) == 2:
                                pkg_name = parts[1].strip(" \"'")
                                rel = str(Path(root).relative_to(self.project_root)).replace(
                                    "\\", "/"
                                )
                                packages[pkg_name] = "" if rel == "." else rel
                                break
                except Exception as e:
                    logger.debug("Failed to parse Cargo.toml at %s: %s", c_path, e)
        return packages

    def _detect_go_module(self) -> str | None:
        """Parse module name from root go.mod if present."""
        return self.go_module

    def _detect_cargo_package(self) -> str | None:
        """Parse package name from root Cargo.toml if present."""
        return self.cargo_package

    def _build_map(self) -> None:
        """Builds a map of module names to relative file paths."""
        src_dirs = {"src", "lib", "python"}

        for root, dirs, files in os.walk(self.project_root):
            dirs[:] = [d for d in dirs if not d.startswith(".") and d != "__pycache__"]
            rel_root = Path(root).relative_to(self.project_root)

            for file in files:
                if not file.endswith(self.SUPPORTED_EXTENSIONS):
                    continue

                rel_path_str = str(rel_root / file).replace("\\", "/")
                if rel_path_str.startswith("./"):
                    rel_path_str = rel_path_str[2:]
                self.known_files.add(rel_path_str)

                name_parts = [p for p in rel_root.parts if p != "."]
                base_name = file.rsplit(".", 1)[0]

                # Absolute mapping
                self._add_to_map(name_parts, base_name, rel_path_str)

                # Strip src_dir if present
                if name_parts and name_parts[0] in src_dirs:
                    self._add_to_map(name_parts[1:], base_name, rel_path_str)

    def _add_to_map(self, parts: list[str], base_name: str, path: str) -> None:
        is_index = base_name in ("__init__", "index") or (
            base_name == "mod" and path.endswith(".rs")
        )
        if is_index and parts:
            folder_mod = ".".join(parts)
            self.module_map[folder_mod] = path

        module_name = ".".join(parts) if base_name == "__init__" else ".".join([*parts, base_name])
        if module_name:
            self.module_map[module_name] = path

    def add_file(self, rel_path: str) -> None:
        """Register a new or updated file in the module resolver."""
        rel_path_str = rel_path.replace("\\", "/")
        if rel_path_str.startswith("./"):
            rel_path_str = rel_path_str[2:]
        if not rel_path_str.endswith(self.SUPPORTED_EXTENSIONS):
            return
        self.known_files.add(rel_path_str)
        path_obj = Path(rel_path_str)
        name_parts = [p for p in path_obj.parent.parts if p != "."]
        base_name = path_obj.stem
        self._add_to_map(name_parts, base_name, rel_path_str)
        src_dirs = {"src", "lib", "python"}
        if name_parts and name_parts[0] in src_dirs:
            self._add_to_map(name_parts[1:], base_name, rel_path_str)

    def remove_file(self, rel_path: str) -> None:
        """Remove a deleted file from the module resolver."""
        rel_path_str = rel_path.replace("\\", "/")
        if rel_path_str.startswith("./"):
            rel_path_str = rel_path_str[2:]
        self.known_files.discard(rel_path_str)
        to_del = [k for k, v in self.module_map.items() if v == rel_path_str]
        for k in to_del:
            del self.module_map[k]

    def resolve(self, module_name: str, current_file: str | None = None) -> str | None:
        """Resolves a module name to a relative path within the project."""
        if not module_name:
            return None

        # Universal TypeScript/Next.js/React path aliases (@/*, ~/*)
        if module_name.startswith(("@/", "~/")):
            sub = module_name[2:]
            for cand in (sub, f"src/{sub}"):
                matched = self._match_known_file(cand, current_file)
                if matched:
                    return matched

        # Go self-module path resolution across root or subproject go.mod
        for g_mod, g_dir in self.go_modules.items():
            if module_name == g_mod or module_name.startswith(f"{g_mod}/"):
                sub = module_name[len(g_mod) + 1 :] if module_name != g_mod else ""
                target_cand = f"{g_dir}/{sub}".strip("/") if g_dir else sub
                if target_cand:
                    matched = self._match_known_file(target_cand, current_file)
                    if matched:
                        return matched
                    for kf in sorted(self.known_files):
                        if (kf.startswith(f"{target_cand}/") or kf == target_cand) and kf.endswith(
                            ".go"
                        ):
                            return kf

        # Rust crate package name resolution across root or subproject Cargo.toml
        for c_pkg, c_dir in self.cargo_packages.items():
            norm_mod = module_name.replace("::", "/").replace(".", "/")
            if norm_mod == c_pkg or norm_mod.startswith(f"{c_pkg}/"):
                sub = norm_mod[len(c_pkg) + 1 :] if norm_mod != c_pkg else ""
                if sub:
                    for cand in (
                        f"{c_dir}/src/{sub}".strip("/"),
                        f"{c_dir}/{sub}".strip("/"),
                        f"src/{sub}",
                        sub,
                    ):
                        matched = self._match_known_file(cand, current_file)
                        if matched:
                            return matched
                for lib_cand in (f"{c_dir}/src/lib.rs", "src/lib.rs"):
                    matched = self._match_known_file(lib_cand, current_file)
                    if matched:
                        return matched

        if module_name.startswith((".", "crate/", "super/")):
            return self._resolve_relative(module_name, current_file)

        res = self.module_map.get(module_name)
        if res:
            return res

        if current_file:
            return self._resolve_relative(f"./{module_name}", current_file)

        return None

    def _resolve_relative(self, module_name: str, current_file: str | None) -> str | None:
        """Resolve Python dotted relative imports and JS/TS path imports."""
        if not current_file:
            return None

        norm_current = current_file.replace("\\", "/")
        current_dir = Path(norm_current).parent

        # Case 0: Rust crate:: and super:: relative module paths
        if module_name.startswith("crate/"):
            sub_path = module_name[6:]
            curr_str = str(current_dir).replace("\\", "/")
            candidates = [
                f"src/{sub_path}",
                sub_path,
                f"{curr_str}/{sub_path}" if curr_str != "." else sub_path,
            ]
            for prefix in candidates:
                norm_p = os.path.normpath(prefix).replace("\\", "/")
                res = self._match_known_file(norm_p, current_file)
                if res:
                    return res
            for kf in sorted(self.known_files):
                if (
                    kf.endswith((f"/{sub_path}.rs", f"/{sub_path}/mod.rs"))
                    or kf == f"{sub_path}.rs"
                    or kf == f"{sub_path}/mod.rs"
                ):
                    return kf

        if module_name.startswith("super/"):
            return self._resolve_relative(f"../{module_name[6:]}", current_file)

        # Case 1: JS/TS slash-based relative import (e.g. './utils', '../components/Node')
        if "/" in module_name:
            raw_target = os.path.normpath(os.path.join(str(current_dir), module_name)).replace(
                "\\", "/"
            )
            if raw_target == ".":
                raw_target = ""
            elif raw_target.startswith("./"):
                raw_target = raw_target[2:]
            return self._match_known_file(raw_target, current_file)

        # Case 2: Python dot-based relative import (e.g. '.', '.base', '..resolver')
        dots = len(module_name) - len(module_name.lstrip("."))
        remainder = module_name[dots:]

        base_dir = current_dir
        for _ in range(dots - 1):
            base_dir = base_dir.parent

        if remainder:
            rel_parts = remainder.split(".")
            target_prefix = "/".join([*(p for p in base_dir.parts if p != "."), *rel_parts])
        else:
            target_prefix = "/".join(p for p in base_dir.parts if p != ".")

        return self._match_known_file(target_prefix, current_file)

    def _match_known_file(self, target_prefix: str, current_file: str | None = None) -> str | None:
        """Match a relative path prefix against indexed project files."""
        if target_prefix in self.known_files:
            return target_prefix

        ext_order = list(self.SUPPORTED_EXTENSIONS)
        if current_file and "." in current_file:
            curr_ext = f".{current_file.rsplit('.', 1)[1]}"
            if curr_ext in ext_order:
                ext_order.remove(curr_ext)
                ext_order.insert(0, curr_ext)

        for ext in ext_order:
            candidate = f"{target_prefix}{ext}"
            if candidate in self.known_files:
                return candidate

        is_rust = current_file and current_file.endswith(".rs")
        is_js_ts = current_file and any(
            current_file.endswith(ext) for ext in (".js", ".jsx", ".ts", ".tsx")
        )
        if is_rust:
            index_bases = ("mod",)
        elif is_js_ts:
            index_bases = ("index", "__init__")
        else:
            index_bases = ("__init__", "index")

        for index_base in index_bases:
            for ext in ext_order:
                candidate = (
                    f"{target_prefix}/{index_base}{ext}" if target_prefix else f"{index_base}{ext}"
                )
                if candidate in self.known_files:
                    return candidate

        pkg_base = target_prefix.split("/")[-1] if target_prefix else ""
        if pkg_base:
            for ext in ext_order:
                candidate = f"{target_prefix}/{pkg_base}{ext}"
                if candidate in self.known_files:
                    return candidate
            for kf in sorted(self.known_files):
                if kf.startswith(f"{target_prefix}/") and any(
                    kf.endswith(ext) for ext in ext_order
                ):
                    return kf

        return None

    def get_module_name(self, file_path: str) -> str | None:
        """Find the canonical module name for a file path."""
        src_dirs = {"src", "lib", "python"}
        norm_path = file_path.replace("\\", "/")
        if norm_path.startswith("./"):
            norm_path = norm_path[2:]
        candidates = [name for name, path in self.module_map.items() if path == norm_path]

        if candidates:
            for c in candidates:
                if not any(c.startswith(sd + ".") for sd in src_dirs) and c not in src_dirs:
                    return c
            return candidates[0]

        # Fallback: compute canonical module name from path segments
        path_obj = Path(norm_path)
        raw_parts = [p for p in path_obj.parent.parts if p not in (".", "")]
        parts = [
            p
            for i, p in enumerate(raw_parts)
            if not (p in src_dirs and (i == 0 or (i == 1 and raw_parts[0] not in src_dirs)))
        ]
        base = path_obj.stem
        if base in ("__init__", "index") or (base == "mod" and norm_path.endswith(".rs")):
            return ".".join(parts) if parts else None
        return ".".join([*parts, base]) if (parts or base) else None
