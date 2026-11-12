"""Global shared constants and traversal limits across VibeTrace."""

from __future__ import annotations

# Token estimation & context budgets
CHARS_PER_TOKEN: float = 3.5
DEFAULT_VIBE_BUNDLE_BUDGET: int = 1500
DEFAULT_REPO_MAP_BUDGET: int = 1024

# Graph traversal & taint depth limits
DEFAULT_MAX_DEPTH: int = 8
MAX_TRAVERSAL_DEPTH: int = 15
DEFAULT_SAVED_TRACE_DEPTH: int = 6

# Directories the codebase never scans (shared by the docs RAG catalog walk
# and the symbol<->doc linker).
IGNORED_DIRS = frozenset(
    {
        ".git",
        ".venv",
        "node_modules",
        "dist",
        "build",
        "scratch",
        "archive",
        "__pycache__",
        ".ruff_cache",
        ".pytest_cache",
        ".gemini",
        ".vscode",
        ".idea",
        "target",
        "tmp",
        "temp",
        "site",
        "htmlcov",
        "extensions",
    }
)
IGNORED_DIRS_CASEFOLDED = frozenset(d.casefold() for d in IGNORED_DIRS)
