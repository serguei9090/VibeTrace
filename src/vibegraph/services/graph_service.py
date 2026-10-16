import os
import sys
from pathlib import Path
from typing import Any, Literal

from vibegraph.indexer.db import IndexerDB
from vibegraph.services.trace_service import (
    CalleeContract,
    CodeSliceResult,
    TraceHop,
    TraceNodeInfo,
    TracePathResult,
    get_code_slice,
    read_file_lines,
    trace_execution_path,
)

__all__ = [
    "CalleeContract",
    "CodeSliceResult",
    "GraphTraverser",
    "TraceHop",
    "TraceNodeInfo",
    "TracePathResult",
    "categorize_file_dependencies",
    "compute_impact_analysis",
    "get_code_slice",
    "get_context_for_path",
    "handle_error",
    "normalize_path",
    "read_file_lines",
    "safe_str",
    "trace_execution_path",
]


def get_context_for_path(path_hint: str | None = None) -> tuple[IndexerDB, Path]:
    """Determine the project root and database connection for a given path."""
    if not path_hint or path_hint == ".":
        search_start = Path.cwd()
    else:
        search_start = Path(path_hint).resolve()

    search_dir = search_start if search_start.is_dir() else search_start.parent
    project_root = search_dir

    for parent in [search_dir, *list(search_dir.parents)]:
        if (
            (parent / ".git").exists()
            or (parent / "pyproject.toml").exists()
            or (parent / "vibegraph_context").exists()
        ):
            project_root = parent
            break

    context_dir = project_root / "vibegraph_context"
    context_dir.mkdir(exist_ok=True)
    db_path = context_dir / "vibegraph.db"

    return IndexerDB(db_path=str(db_path)), project_root


def normalize_path(file_path: str, project_root: Path) -> str:
    """Normalize file path relative to the identified project root."""
    try:
        abs_file = Path(file_path).resolve()
        abs_root = project_root.resolve()
        return str(abs_file.relative_to(abs_root)).replace("\\", "/")
    except (ValueError, RuntimeError):
        return str(Path(file_path).resolve()).replace("\\", "/")


def safe_str(s: str) -> str:
    """Safely format string for output, replacing emojis on Windows if needed."""
    if os.name != "nt":
        return s

    replacements = {
        "🔄": "[CYCLE]",
        "←": "<-",
        "→": "->",
        "✅": "[OK]",
    }
    for char, replacement in replacements.items():
        s = s.replace(char, replacement)
    return s


def handle_error(e: Exception, context: str = "") -> str:
    """Consistent error formatting across all tools."""
    error_type = type(e).__name__
    if context:
        return f"Error ({error_type}): {context} - {e}"
    return f"Error ({error_type}): {e}"


class GraphTraverser:
    """Traverses the call graph and builds formatted trace lines."""

    def __init__(self, db: IndexerDB):
        self.db = db
        self.visited: set[str] = set()
        self.output: list[str] = []

    def traverse(
        self,
        current_id: str,
        depth: int,
        max_depth: int,
        direction: Literal["up", "down"],
        indent: int,
        path_stack: list[str] | None = None,
    ) -> None:
        """Recursively traverse the call graph."""
        if depth > max_depth:
            return

        if path_stack is None:
            path_stack = []

        if current_id in self.visited:
            msg = safe_str("🔄 [CYCLE DETECTED - circular dependency]")
            self.output.append(f"{'  ' * indent}{msg}")
            return

        self.visited.add(current_id)

        prefix = safe_str("← called by") if direction == "up" else safe_str("→ calls")
        neighbors = self.db.get_neighbors(current_id, direction)

        for n in neighbors:
            current_path = [*path_stack, n["name"]]
            breadcrumb = " > ".join(current_path)

            line = (
                f"{'  ' * indent}- {prefix} `{breadcrumb}` ({n['relation_type']}) "
                f"in `{n['file_path']}`"
            )
            self.output.append(line)
            self.traverse(
                n["neighbor_id"], depth + 1, max_depth, direction, indent + 1, current_path
            )


def compute_impact_analysis(
    db: IndexerDB, normalized_path: str
) -> tuple[int, dict[int, list[str]]]:
    """Compute 3-level transitive impact for changes to a file."""
    file_nodes = db.get_nodes_in_file(normalized_path)
    if not file_nodes:
        return 0, {}

    queue = [(n["id"], 0, n["name"]) for n in file_nodes]
    visited = {n["id"] for n in file_nodes}

    impacts_by_level: dict[int, list[str]] = {1: [], 2: [], 3: []}
    total_impact = 0

    while queue:
        curr_id, depth, path_desc = queue.pop(0)
        if depth >= 3:
            continue

        dependents = db.get_incoming_dependents(curr_id)

        for dep in dependents:
            if dep["id"] in visited:
                continue

            next_depth = depth + 1
            if dep["file_path"] != normalized_path:
                if next_depth <= 3:
                    entry = (
                        f"- **`{dep['name']}`** (`{dep['file_path']}`) "
                        f"depends on `{path_desc}` via `{dep['relation_type']}`"
                    )
                    impacts_by_level[next_depth].append(entry)
                    total_impact += 1

            visited.add(dep["id"])
            queue.append((dep["id"], next_depth, dep["name"]))

    return total_impact, impacts_by_level


def categorize_file_dependencies(
    db: IndexerDB, normalized_path: str
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]]]:
    """Categorize dependencies into internal, third-party, and standard library."""
    deps = db.get_file_imports(normalized_path)

    try:
        stdlib_names = sys.stdlib_module_names
    except Exception:
        stdlib_names = {
            "os",
            "sys",
            "pathlib",
            "json",
            "typing",
            "subprocess",
            "hashlib",
            "re",
            "math",
            "datetime",
            "sqlite3",
            "abc",
        }

    internal: list[dict[str, Any]] = []
    stdlib: list[dict[str, Any]] = []
    third_party: list[dict[str, Any]] = []

    for dep in deps:
        name = dep["name"]
        path = dep["file_path"]

        if path != "external":
            internal.append(dep)
        else:
            root_pkg = name.split(".")[0]
            if root_pkg in stdlib_names:
                stdlib.append(dep)
            else:
                third_party.append(dep)

    return internal, third_party, stdlib
