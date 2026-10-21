from __future__ import annotations

from enum import Enum
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class ResponseFormat(str, Enum):
    """Output format for tool responses."""

    MARKDOWN = "markdown"
    JSON = "json"


class TraceDirection(str, Enum):
    """Direction for call stack tracing."""

    UP = "up"
    DOWN = "down"
    BOTH = "both"


class StructuralSummaryInput(BaseModel):
    """Input model for structural summary operations."""

    model_config = ConfigDict(str_strip_whitespace=True, validate_assignment=True)

    file_path: str = Field(
        ...,
        description="Path to the file to analyze (e.g., 'src/parser.py')",
        min_length=1,
    )
    limit: int = Field(
        default=100,
        description="Maximum number of nodes to return",
        ge=1,
        le=500,
    )
    offset: int = Field(
        default=0,
        description="Number of nodes to skip for pagination",
        ge=0,
    )
    response_format: ResponseFormat = Field(
        default=ResponseFormat.MARKDOWN,
        description="Output format: 'markdown' for human-readable or 'json' for machine-readable",
    )


class CallStackInput(BaseModel):
    """Input model for call stack tracing operations."""

    model_config = ConfigDict(str_strip_whitespace=True, validate_assignment=True)

    node_name: str = Field(
        ...,
        description="Name of the function or class to trace (e.g., 'parse_file', 'IndexerDB')",
        min_length=1,
    )
    file_path: str | None = Field(
        default=None,
        description="Optional file path to disambiguate if multiple nodes have the same name",
    )
    direction: TraceDirection = Field(
        default=TraceDirection.BOTH,
        description="Trace direction: 'up' (callers), 'down' (callees), or 'both'",
    )
    depth: int = Field(
        default=1,
        description="Maximum traversal depth (how many levels to trace)",
        ge=1,
        le=10,
    )


class ImpactAnalysisInput(BaseModel):
    """Input model for impact analysis operations."""

    model_config = ConfigDict(str_strip_whitespace=True, validate_assignment=True)

    file_path: str = Field(
        ...,
        description="Path to the file to analyze for impact (e.g., 'src/db.py')",
        min_length=1,
    )


class ReindexInput(BaseModel):
    """Input model for reindexing operations."""

    model_config = ConfigDict(str_strip_whitespace=True, validate_assignment=True)

    path: str = Field(
        default=".",
        description=(
            "Absolute path to the project root, directory, or file "
            "(Relative paths may fail context resolution)"
        ),
    )
    force: bool = Field(
        default=False,
        description="Force re-indexing all files by bypassing Git-SHA delta cache",
    )
    use_lsp: bool = Field(
        default=False,
        description="Enable dynamic LSP hybrid bridge for type inference and re-exports",
    )


class ReferencesInput(BaseModel):
    """Input model for finding reference operations."""

    model_config = ConfigDict(str_strip_whitespace=True, validate_assignment=True)

    symbol_name: str = Field(
        ...,
        description="Name of the function/class/variable to find references for",
        min_length=1,
    )
    scope_path: str = Field(
        default=".",
        description="Project root or relevant path to scope the search context (defaults to CWD)",
    )


class DependenciesInput(BaseModel):
    """Input model for dependency analysis operations."""

    model_config = ConfigDict(str_strip_whitespace=True, validate_assignment=True)

    file_path: str = Field(
        ...,
        description="Path to the file to check for outgoing dependencies",
        min_length=1,
    )


class SearchInput(BaseModel):
    """Input model for signature search operations."""

    model_config = ConfigDict(str_strip_whitespace=True, validate_assignment=True)

    pattern: str = Field(
        ...,
        description=(
            "SQL pattern to search for in node signatures (e.g. '%List[str]%', 'async def%')"
        ),
        min_length=1,
    )
    scope_path: str = Field(
        default=".",
        description="Project root or relevant path to scope the search context (defaults to CWD)",
    )


class SemanticSearchInput(BaseModel):
    """Input model for hybrid RRF semantic + lexical + AST search."""

    model_config = ConfigDict(str_strip_whitespace=True, validate_assignment=True)

    query: str = Field(
        ...,
        description="Natural language intent, symbol name, or keyword query",
        min_length=1,
    )
    limit: int = Field(
        default=15,
        description="Maximum number of ranked symbol results to return",
        ge=1,
        le=100,
    )
    scope_path: str = Field(
        default=".",
        description="Project root or relevant path to scope the search context",
    )
    response_format: ResponseFormat = Field(
        default=ResponseFormat.MARKDOWN,
        description="Output format: 'markdown' or 'json'",
    )


class TracePathInput(BaseModel):
    """Input model for bidirectional execution tracepath discovery."""

    model_config = ConfigDict(str_strip_whitespace=True, validate_assignment=True)

    from_symbol: str = Field(
        ...,
        description="Starting symbol or qualified name (e.g. 'process_checkout')",
        min_length=1,
    )
    to_symbol: str = Field(
        ...,
        description="Target symbol or qualified name (e.g. 'save_invoice')",
        min_length=1,
    )
    max_depth: int = Field(
        default=6,
        description="Maximum hop depth for Bidirectional BFS",
        ge=1,
        le=15,
    )
    scope_path: str = Field(
        default=".",
        description="Project root or relevant path to scope the search context",
    )
    response_format: ResponseFormat = Field(
        default=ResponseFormat.MARKDOWN,
        description="Output format: 'markdown' or 'json'",
    )
    engine: Literal["bfs", "cte"] = Field(
        default="bfs",
        description="Path engine: 'bfs' (bidirectional BFS) or 'cte' (native SQLite CTE)",
    )


class CodeSliceInput(BaseModel):
    """Input model for surgical source code line slicing."""

    model_config = ConfigDict(str_strip_whitespace=True, validate_assignment=True)

    symbol_name: str = Field(
        ...,
        description="Symbol name or scoped qualified_name (e.g. 'IndexerDB.upsert_node')",
        min_length=1,
    )
    file_path: str | None = Field(
        default=None,
        description="Optional file path to disambiguate symbols with identical short names",
    )
    include_callees: bool = Field(
        default=True,
        description="Whether to include direct outgoing callee signatures and call lines",
    )
    scope_path: str = Field(
        default=".",
        description="Project root or relevant path to scope the search context",
    )
    response_format: ResponseFormat = Field(
        default=ResponseFormat.MARKDOWN,
        description="Output format: 'markdown' or 'json'",
    )


class CallTreeInput(BaseModel):
    """Input model for recursive top-down execution call tree exploration."""

    model_config = ConfigDict(str_strip_whitespace=True, validate_assignment=True)

    symbol_name: str = Field(
        ...,
        description="Root symbol or qualified name to trace downward (e.g. 'main')",
        min_length=1,
    )
    max_depth: int = Field(
        default=3,
        description="Maximum depth of recursive callee expansion (default: 3)",
        ge=1,
        le=10,
    )
    file_path: str | None = Field(
        default=None,
        description="Optional file path to disambiguate symbols with identical short names",
    )
    scope_path: str = Field(
        default=".",
        description="Project root or relevant path to scope the search context",
    )
    response_format: ResponseFormat = Field(
        default=ResponseFormat.MARKDOWN,
        description="Output format: 'markdown' or 'json'",
    )
