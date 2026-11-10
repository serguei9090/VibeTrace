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
