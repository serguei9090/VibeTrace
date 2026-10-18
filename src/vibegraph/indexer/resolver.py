import os
from pathlib import Path


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
        self._build_map()

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
        if base_name == "__init__":
            module_name = ".".join(parts)
        else:
            module_name = ".".join([*parts, base_name])

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

        if module_name.startswith("."):
            return self._resolve_relative(module_name, current_file)

        return self.module_map.get(module_name)

    def _resolve_relative(self, module_name: str, current_file: str | None) -> str | None:
        """Resolve Python dotted relative imports and JS/TS path imports."""
        if not current_file:
            return None

        norm_current = current_file.replace("\\", "/")
        current_dir = Path(norm_current).parent

        # Case 1: JS/TS slash-based relative import (e.g. './utils', '../components/Node')
        if "/" in module_name:
            raw_target = os.path.normpath(os.path.join(str(current_dir), module_name)).replace(
                "\\", "/"
            )
            if raw_target.startswith("./"):
                raw_target = raw_target[2:]
            return self._match_known_file(raw_target)

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

        return self._match_known_file(target_prefix)

    def _match_known_file(self, target_prefix: str) -> str | None:
        """Match a relative path prefix against indexed project files."""
        if target_prefix in self.known_files:
            return target_prefix

        for ext in self.SUPPORTED_EXTENSIONS:
            candidate = f"{target_prefix}{ext}"
            if candidate in self.known_files:
                return candidate

        for index_base in ("__init__", "index"):
            for ext in self.SUPPORTED_EXTENSIONS:
                candidate = (
                    f"{target_prefix}/{index_base}{ext}" if target_prefix else f"{index_base}{ext}"
                )
                if candidate in self.known_files:
                    return candidate

        return None

    def get_module_name(self, file_path: str) -> str | None:
        """Find the canonical module name for a file path."""
        src_dirs = {"src", "lib", "python"}
        norm_path = file_path.replace("\\", "/")
        candidates = [name for name, path in self.module_map.items() if path == norm_path]

        if not candidates:
            return None

        for c in candidates:
            if not any(c.startswith(sd + ".") for sd in src_dirs) and c not in src_dirs:
                return c

        return candidates[0]
