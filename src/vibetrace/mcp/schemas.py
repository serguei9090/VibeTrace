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
    scope_path: str = Field(
        default=".",
        description="Project root or relevant path to scope the search context (defaults to CWD)",
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
    all_relations: bool = Field(
        default=False,
        description="Include non-executable AST relations (defines, imports, references)",
    )
    scope_path: str = Field(
        default=".",
        description="Project root or relevant path to scope the search context (defaults to CWD)",
    )
    response_format: ResponseFormat = Field(
        default=ResponseFormat.MARKDOWN,
        description="Output format: 'markdown' or 'json'",
    )


class ImpactAnalysisInput(BaseModel):
    """Input model for impact analysis operations."""

    model_config = ConfigDict(str_strip_whitespace=True, validate_assignment=True)

    file_path: str = Field(
        ...,
        description="Path to the file to analyze for impact (e.g., 'src/db.py')",
        min_length=1,
    )
    scope_path: str = Field(
        default=".",
        description="Project root or relevant path to scope the search context (defaults to CWD)",
    )
    response_format: ResponseFormat = Field(
        default=ResponseFormat.MARKDOWN,
        description="Output format: 'markdown' or 'json'",
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
    embed: bool = Field(
        default=False,
        description="Compute and store 256d Matryoshka vector embeddings after indexing",
    )


class ReferencesInput(BaseModel):
    """Input model for finding reference operations."""

    model_config = ConfigDict(str_strip_whitespace=True, validate_assignment=True)

    symbol_name: str = Field(
        ...,
        description="Name of the function/class/variable to find references for",
        min_length=1,
    )
    file_path: str | None = Field(
        default=None,
        description="Optional file path to disambiguate if multiple nodes share the same name",
    )
    kind: str | None = Field(
        default=None,
        description=(
            "Optional filter by relation kind: 'calls', 'references', 'inherits', "
            "'implements', 'routes'"
        ),
    )
    scope_path: str = Field(
        default=".",
        description="Project root or relevant path to scope the search context (defaults to CWD)",
    )
    response_format: ResponseFormat = Field(
        default=ResponseFormat.MARKDOWN,
        description="Output format: 'markdown' or 'json'",
    )


class DependenciesInput(BaseModel):
    """Input model for dependency analysis operations."""

    model_config = ConfigDict(str_strip_whitespace=True, validate_assignment=True)

    file_path: str = Field(
        ...,
        description="Path to the file to check for outgoing dependencies",
        min_length=1,
    )
    reverse: bool = Field(
        default=False,
        description="Show only inbound dependents (files that import this file)",
    )
    scope_path: str = Field(
        default=".",
        description="Project root or relevant path to scope the search context (defaults to CWD)",
    )
    response_format: ResponseFormat = Field(
        default=ResponseFormat.MARKDOWN,
        description="Output format: 'markdown' or 'json'",
    )


class ListSavedTracesInput(BaseModel):
    """Input model for listing saved architectural trace queries."""

    model_config = ConfigDict(str_strip_whitespace=True, validate_assignment=True)

    scope_path: str = Field(
        default=".",
        description="Project root or relevant path to scope the search context (defaults to CWD)",
    )
    response_format: ResponseFormat = Field(
        default=ResponseFormat.MARKDOWN,
        description="Output format: 'markdown' or 'json'",
    )


class SaveTraceInput(BaseModel):
    """Input model for persisting a saved architectural trace query."""

    model_config = ConfigDict(str_strip_whitespace=True, validate_assignment=True)

    name: str = Field(
        ...,
        description="Unique persistent name for this trace query",
        min_length=1,
        max_length=100,
    )
    from_symbol: str = Field(..., description="Source starting symbol or endpoint", min_length=1)
    to_symbol: str = Field(..., description="Target destination symbol", min_length=1)
    description: str = Field(default="", description="Optional architectural description")
    max_depth: int = Field(default=6, description="Maximum traversal depth", ge=1, le=20)
    scope_path: str = Field(default=".", description="Project root directory path")


class DeleteSavedTraceInput(BaseModel):
    """Input model for deleting a saved architectural trace query."""

    model_config = ConfigDict(str_strip_whitespace=True, validate_assignment=True)

    name: str = Field(..., description="Name of the saved trace to delete", min_length=1)
    scope_path: str = Field(default=".", description="Project root directory path")


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
    limit: int = Field(
        default=50,
        description="Maximum number of matches to return",
        ge=1,
        le=200,
    )
    offset: int = Field(
        default=0,
        description="Number of matches to skip for pagination",
        ge=0,
    )
    response_format: ResponseFormat = Field(
        default=ResponseFormat.MARKDOWN,
        description="Output format: 'markdown' or 'json'",
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
    offset: int = Field(
        default=0,
        description="Number of matches to skip for pagination",
        ge=0,
    )
    scope_path: str = Field(
        default=".",
        description="Project root or relevant path to scope the search context",
    )
    response_format: ResponseFormat = Field(
        default=ResponseFormat.MARKDOWN,
        description="Output format: 'markdown' or 'json'",
    )
    include_docs: bool = Field(
        default=False,
        description="Whether to include relevant documentation chunks alongside code symbols",
    )


class TracePathInput(BaseModel):
    """Input model for bidirectional execution tracepath discovery."""

    model_config = ConfigDict(str_strip_whitespace=True, validate_assignment=True)

    from_symbol: str | None = Field(
        default=None,
        description="Starting symbol or qualified name (e.g. 'process_checkout')",
    )
    to_symbol: str | None = Field(
        default=None,
        description="Target symbol or qualified name (e.g. 'save_invoice')",
    )
    saved_name: str | None = Field(
        default=None,
        description="Execute a persistent named saved trace query (e.g. 'AuthFlow')",
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


class DocSearchInput(BaseModel):
    """Input model for searching across documentation catalogs."""

    model_config = ConfigDict(str_strip_whitespace=True, validate_assignment=True)

    query: str = Field(
        ...,
        description="Search query string to search documentation with hybrid RRF",
        min_length=1,
    )
    catalog: str | None = Field(
        default=None,
        description="Optional specific catalog name to filter search",
    )
    scope: Literal["local", "global", "all"] = Field(
        default="local",
        description="Catalog search scope: 'local' (default), 'global', or 'all'",
    )
    scope_path: str = Field(
        default=".",
        description="Project root or relevant path to scope the search context",
    )
    limit: int = Field(
        default=5,
        ge=1,
        le=50,
        description="Maximum number of documentation chunks to return",
    )
    response_format: ResponseFormat = Field(
        default=ResponseFormat.MARKDOWN,
        description="Output format: 'markdown' or 'json'",
    )


class DocCatalogsInput(BaseModel):
    """Input model for listing documentation catalogs."""

    model_config = ConfigDict(str_strip_whitespace=True, validate_assignment=True)

    scope_path: str = Field(
        default=".",
        description="Project root or relevant path to scope the context",
    )
    response_format: ResponseFormat = Field(
        default=ResponseFormat.MARKDOWN,
        description="Output format: 'markdown' or 'json'",
    )


class CallerGuardInput(BaseModel):
    """Input model for pre-flight caller blast-radius guard."""

    model_config = ConfigDict(str_strip_whitespace=True, validate_assignment=True)

    symbol_name: str = Field(
        default="",
        description="Target symbol name to inspect callers of (optional if staged=True)",
    )
    staged: bool = Field(
        default=False,
        description="Inspect modified symbol definitions in staged git diff",
    )
    strict: bool = Field(
        default=False,
        description="In staged mode, raise warning alert if any caller exists",
    )
    file_path: str | None = Field(
        default=None,
        description="Optional file path containing the symbol to disambiguate identical names",
    )
    scope_path: str = Field(
        default=".",
        description="Project root or relevant path to scope the context",
    )
    response_format: ResponseFormat = Field(
        default=ResponseFormat.MARKDOWN,
        description="Output format: 'markdown' or 'json'",
    )


class VibeBundleInput(BaseModel):
    """Input model for one-shot task context bundle."""

    model_config = ConfigDict(str_strip_whitespace=True, validate_assignment=True)

    intent: str = Field(
        ...,
        description="Natural language task description or query (e.g. 'add auth token')",
        min_length=1,
    )
    target_symbol: str | None = Field(
        default=None,
        description="Optional explicit symbol override to focus code slice and caller guard",
    )
    token_budget: int = Field(
        default=1500,
        ge=200,
        le=8000,
        description="Maximum token budget for context bundle (default: 1500)",
    )
    scope_path: str = Field(
        default=".",
        description="Project root or relevant path to scope the context",
    )
    response_format: ResponseFormat = Field(
        default=ResponseFormat.MARKDOWN,
        description="Output format: 'markdown' or 'json'",
    )


class ProjectStatusInput(BaseModel):
    """Input model for inspecting federated multi-repo project status."""

    model_config = ConfigDict(str_strip_whitespace=True, validate_assignment=True)

    scope_path: str = Field(
        default=".",
        description="Project root or member repository path",
    )
    response_format: ResponseFormat = Field(
        default=ResponseFormat.MARKDOWN,
        description="Output format: 'markdown' or 'json'",
    )


class ProjectSearchInput(BaseModel):
    """Input model for federated symbol search across member repositories."""

    model_config = ConfigDict(str_strip_whitespace=True, validate_assignment=True)

    query: str = Field(..., description="Symbol or function name to search for", min_length=1)
    repo: str | None = Field(default=None, description="Optional member repository filter")
    limit: int = Field(default=20, ge=1, le=100, description="Maximum results to return")
    scope_path: str = Field(default=".", description="Project root path")
    response_format: ResponseFormat = Field(
        default=ResponseFormat.MARKDOWN,
        description="Output format: 'markdown' or 'json'",
    )


class ProjectGuardInput(BaseModel):
    """Input model for cross-repository blast-radius caller inspection."""

    model_config = ConfigDict(str_strip_whitespace=True, validate_assignment=True)

    symbol: str = Field(
        ...,
        description="Target symbol to inspect for cross-repo callers",
        min_length=1,
    )
    repo: str | None = Field(default=None, description="Optional repository defining the symbol")
    scope_path: str = Field(default=".", description="Project root path")
    response_format: ResponseFormat = Field(
        default=ResponseFormat.MARKDOWN,
        description="Output format: 'markdown' or 'json'",
    )


class ProjectLinksInput(BaseModel):
    """Input model for discovering cross-repo HTTP routes and RPC contracts."""

    model_config = ConfigDict(str_strip_whitespace=True, validate_assignment=True)

    scope_path: str = Field(default=".", description="Project root path")
    response_format: ResponseFormat = Field(
        default=ResponseFormat.MARKDOWN,
        description="Output format: 'markdown' or 'json'",
    )


class AuditSplitsInput(BaseModel):
    """Input model for auditing cross-module execution splits against baseline."""

    model_config = ConfigDict(str_strip_whitespace=True, validate_assignment=True)

    symbol: str | None = Field(
        default=None,
        description="Optional symbol to filter audit results (e.g. 'find_nodes_by_name')",
    )
    scope_path: str = Field(default=".", description="Project root path")
    response_format: ResponseFormat = Field(
        default=ResponseFormat.MARKDOWN,
        description="Output format: 'markdown' or 'json'",
    )


class ApproveSplitInput(BaseModel):
    """Input model for recording approval of an architectural split in .vibetrace/splits.toml."""

    model_config = ConfigDict(str_strip_whitespace=True, validate_assignment=True)

    symbol: str = Field(..., description="Function or method symbol name", min_length=1)
    code_hash: str = Field(..., description="SHA-256 hash of the normalized function body")
    qualified_name: str | None = Field(default=None, description="Optional qualified symbol name")
    file_path: str = Field(default="", description="Source file path")
    branches: list[str] = Field(
        default_factory=list, description="Target outgoing branches or callees"
    )
    status: str = Field(
        default="approved",
        description="Review status: 'approved' or 'risk'",
    )
    rationale: str = Field(
        default="Architectural review verified",
        description="Explanation or review justification",
    )
    reviewed_by: str = Field(
        default="developer",
        description="Name or ID of reviewer / agent",
    )
    scope_path: str = Field(default=".", description="Project root path")
    response_format: ResponseFormat = Field(
        default=ResponseFormat.MARKDOWN,
        description="Output format: 'markdown' or 'json'",
    )


class AuditSecurityInput(BaseModel):
    """Input model for auditing security vulnerabilities across the codebase and lockfiles."""

    model_config = ConfigDict(str_strip_whitespace=True, validate_assignment=True)

    file_path: str | None = Field(
        default=None,
        description="Optional specific file to audit (e.g. 'src/api.py')",
    )
    skip_deps: bool = Field(
        default=False,
        description="Skip lockfile package CVE resolution (AST code scan only)",
    )
    no_taint: bool = Field(
        default=False,
        description="Disable call-graph taint reachability escalation",
    )
    update: bool = Field(
        default=False,
        description="Force refresh vulnerability intelligence from Google OSV",
    )
    scope_path: str = Field(default=".", description="Project root path")
    response_format: ResponseFormat = Field(
        default=ResponseFormat.MARKDOWN,
        description="Output format: 'markdown' or 'json'",
    )


class ApproveSecurityExceptionInput(BaseModel):
    """Input model for recording a security finding exception into .vibetrace/security.toml."""

    model_config = ConfigDict(str_strip_whitespace=True, validate_assignment=True)

    finding_id: str = Field(..., description="Unique ID of the finding (e.g. 'SEC-CWE-78-...')")
    rule_id: str = Field(..., description="Standardized rule identifier (e.g. 'py:CWE-78')")
    file_path: str = Field(..., description="Path to the file containing the vulnerability")
    code_hash: str = Field(..., description="SHA-256 hash of the vulnerable snippet")
    rationale: str = Field(..., description="Explanation or justification for accepting this risk")
    severity: str = Field(default="HIGH", description="Severity tier: CRITICAL, HIGH, MEDIUM, LOW")
    status: str = Field(default="APPROVED", description="Status: APPROVED or RISK")
    reviewed_by: str = Field(default="agent", description="Reviewer identifier or agent name")
    scope_path: str = Field(default=".", description="Project root path")
    response_format: ResponseFormat = Field(
        default=ResponseFormat.MARKDOWN,
        description="Output format: 'markdown' or 'json'",
    )


class RemediateVulnerabilityInput(BaseModel):
    """Input model for generating structured remediation prompts and patches for findings."""

    model_config = ConfigDict(str_strip_whitespace=True, validate_assignment=True)

    finding_id: str | None = Field(
        default=None,
        description="Optional specific finding ID (e.g. 'SEC-CWE-78-...').",
    )
    file_path: str | None = Field(
        default=None,
        description="Optional file filter for remediation",
    )
    scope_path: str = Field(default=".", description="Project root path")
    response_format: ResponseFormat = Field(
        default=ResponseFormat.MARKDOWN,
        description="Output format: 'markdown' or 'json'",
    )
