import os
import sys
from collections import deque
from pathlib import Path
from typing import Any, Literal

from vibetrace.indexer.db import IndexerDB
from vibetrace.services.call_tree_service import (
    CallTreeNode,
    CallTreeResult,
    build_call_tree,
    format_call_tree_md,
)
from vibetrace.services.stdlib_registry import get_python_stdlib_names, is_stdlib_import
from vibetrace.services.trace_service import (
    CalleeContract,
    CodeSliceResult,
    TraceHop,
    TraceNodeInfo,
    TracePathResult,
    get_code_slice,
    notify_ui_focus,
    read_file_lines,
    trace_execution_path,
)

__all__ = [
    "CallTreeNode",
    "CallTreeResult",
    "CalleeContract",
    "CodeSliceResult",
    "GraphTraverser",
    "TraceHop",
    "TraceNodeInfo",
    "TracePathResult",
    "build_call_tree",
    "categorize_file_dependencies",
    "compute_impact_analysis",
    "ensure_index_fresh",
    "format_call_tree_md",
    "get_code_slice",
    "get_context_for_path",
    "handle_error",
    "normalize_path",
    "notify_ui_focus",
    "read_file_lines",
    "safe_str",
    "trace_execution_path",
]


import time

from vibetrace.indexer.git_utils import get_git_delta, get_git_sha

_LAST_SYNC_CHECK: dict[str, float] = {}


def ensure_index_fresh(
    db: IndexerDB,
    project_root: Path,
    debounce_seconds: float = 2.0,
    verbose: bool = False,
) -> bool:
    """
    Ensure the IndexerDB is synchronized with the latest working tree changes.
    Uses Git-SHA delta caching (<20ms).
    Debounced by debounce_seconds to prevent duplicate checks during burst queries.
    """
    from vibetrace.indexer.main import reindex_all

    key = str(project_root.resolve())
    now = time.time()
    if now - _LAST_SYNC_CHECK.get(key, 0.0) < debounce_seconds:
        return False
    _LAST_SYNC_CHECK[key] = now

    current_sha = get_git_sha(project_root)
    last_sha = db.get_meta("last_indexed_git_sha")

    # If repository has never been indexed, perform initial indexing
    if not last_sha:
        reindex_all(db, str(project_root), verbose=verbose, project_root=project_root)
        return True

    # If in non-git workspace, initial indexing was already completed
    if last_sha == "non-git" and not current_sha:
        return False

    # If in git repo, check for delta
    if current_sha:
        if current_sha != last_sha:
            delta = get_git_delta(project_root, last_sha)
            if delta is None or delta[0] or delta[1]:
                reindex_all(db, str(project_root), verbose=verbose, project_root=project_root)
                return True
        else:
            delta = get_git_delta(project_root, current_sha)
            if delta is not None and (delta[0] or delta[1]):
                reindex_all(db, str(project_root), verbose=verbose, project_root=project_root)
                return True

    return False


def get_context_for_path(
    path_hint: str | None = None,
    auto_sync: bool = True,
    root_override: str | Path | None = None,
) -> tuple[IndexerDB, Path]:
    """Determine the project root and database connection for a given path."""
    if root_override:
        project_root = Path(root_override).resolve()
    else:
        env_root = os.environ.get("VIBETRACE_PROJECT_ROOT")
        if (not path_hint or path_hint == ".") and env_root:
            project_root = Path(env_root).resolve()
        else:
            if not path_hint or path_hint == ".":
                search_start = Path.cwd()
            else:
                search_start = Path(path_hint).resolve()

            search_dir = search_start if search_start.is_dir() else search_start.parent
            project_root = search_dir

            found = False
            for parent in [search_dir, *list(search_dir.parents)]:
                if (
                    (parent / ".git").exists()
                    or (parent / "pyproject.toml").exists()
                    or (parent / "package.json").exists()
                    or (parent / "Cargo.toml").exists()
                    or (parent / "go.mod").exists()
                    or (parent / ".vibetrace.toml").exists()
                    or (parent / "vibetrace.toml").exists()
                    or (parent / ".vibetrace").exists()
                    or (parent / "vibetrace_context").exists()
                ):
                    project_root = parent
                    found = True
                    break

            if not found and env_root:
                project_root = Path(env_root).resolve()

    from vibetrace.config import load_config

    cfg = load_config(project_root)
    cfg_db = (project_root / cfg.database.path).resolve()

    if (project_root / ".vibetrace" / "vibetrace.db").exists():
        db_path = project_root / ".vibetrace" / "vibetrace.db"
    elif (project_root / ".vibetrace.toml").exists() or cfg_db.exists():
        db_path = cfg_db
        db_path.parent.mkdir(parents=True, exist_ok=True)
    elif (project_root / ".vibetrace").exists() and not (
        project_root / "vibetrace_context"
    ).exists():
        context_dir = project_root / ".vibetrace"
        context_dir.mkdir(exist_ok=True)
        db_path = context_dir / "vibetrace.db"
    else:
        context_dir = project_root / "vibetrace_context"
        context_dir.mkdir(exist_ok=True)
        db_path = context_dir / "vibetrace.db"

    db = IndexerDB(db_path=str(db_path))
    if auto_sync and cfg.indexer.auto_sync:
        ensure_index_fresh(db, project_root, debounce_seconds=cfg.indexer.debounce_seconds)

    return db, project_root


def normalize_path(file_path: str, project_root: Path) -> str:
    """Normalize file path relative to the identified project root."""
    try:
        p = Path(file_path)
        abs_root = project_root.resolve()
        if p.is_absolute():
            abs_file = p.resolve()
        elif p.exists():
            abs_file = p.resolve()
        else:
            abs_file = (abs_root / p).resolve()
        return str(abs_file.relative_to(abs_root)).replace("\\", "/")
    except (ValueError, RuntimeError):
        return str(Path(file_path).resolve()).replace("\\", "/")


def safe_str(s: str) -> str:
    """Safely format string for output, replacing emojis and box characters if needed."""
    if os.name != "nt":
        return s

    encoding = getattr(sys.stdout, "encoding", None) or "utf-8"
    try:
        s.encode(encoding)
        return s
    except (UnicodeEncodeError, LookupError):
        replacements = {
            "🔄": "[CYCLE]",
            "←": "<-",
            "→": "->",
            "✅": "[OK]",
            "—": "--",
            "├──": "+--",
            "└──": "\\--",
            "│": "|",
            "─": "-",
        }
        for char, replacement in replacements.items():
            s = s.replace(char, replacement)
        return s.encode(encoding, errors="replace").decode(encoding, errors="replace")


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
        ancestor_ids: set[str] | None = None,
    ) -> None:
        """Recursively traverse the call graph."""
        if depth > max_depth:
            return

        if path_stack is None:
            path_stack = []
        if ancestor_ids is None:
            ancestor_ids = set()

        if current_id in ancestor_ids:
            msg = safe_str("🔄 [CYCLE DETECTED - circular dependency]")
            self.output.append(f"{'  ' * indent}{msg}")
            return

        if current_id in self.visited:
            msg = safe_str("↪ [already traversed in another branch]")
            self.output.append(f"{'  ' * indent}{msg}")
            return

        self.visited.add(current_id)
        next_ancestors = ancestor_ids | {current_id}

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
                n["neighbor_id"],
                depth + 1,
                max_depth,
                direction,
                indent + 1,
                current_path,
                next_ancestors,
            )


def compute_impact_analysis(
    db: IndexerDB, normalized_path: str
) -> tuple[int, dict[int, list[str]]]:
    """Compute 3-level transitive impact for changes to a file."""
    norm_p = normalized_path.replace("\\", "/").removeprefix("./")
    file_nodes = db.get_nodes_in_file(norm_p)
    if not file_nodes:
        return 0, {}

    queue = deque((n["id"], 0, n["name"]) for n in file_nodes)
    visited = {n["id"] for n in file_nodes}

    impacts_by_level: dict[int, list[str]] = {1: [], 2: [], 3: []}
    total_impact = 0

    while queue:
        curr_id, depth, path_desc = queue.popleft()
        if depth >= 3:
            continue

        dependents = db.get_incoming_dependents(curr_id)

        for dep in dependents:
            if dep["id"] in visited:
                continue

            next_depth = depth + 1
            if dep["file_path"] != norm_p:
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
    stdlib_names = get_python_stdlib_names()

    internal: list[dict[str, Any]] = []
    stdlib: list[dict[str, Any]] = []
    third_party: list[dict[str, Any]] = []

    for dep in deps:
        name = dep["name"]
        path = dep["file_path"]

        if path != "external":
            internal.append(dep)
        else:
            if is_stdlib_import(name, normalized_path, stdlib_names):
                stdlib.append(dep)
            else:
                third_party.append(dep)

    return internal, third_party, stdlib
