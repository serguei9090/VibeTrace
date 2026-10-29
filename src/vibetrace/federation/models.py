"""Pydantic data models for multi-repository project workspace federation."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field


class RepoMember(BaseModel):
    """Specification of a repository participating in a project workspace."""

    name: str = Field(..., min_length=1, max_length=64, description="Unique repository identifier")
    path: str = Field(..., min_length=1, description="Relative or absolute path to repo directory")
    description: str = Field(default="", description="Human-readable description of repository")
    storage_path: str = Field(
        default=".vibetrace/vibetrace.db",
        description="Relative path from repo root to its SQLite database",
    )


class ProjectSettings(BaseModel):
    """Configuration settings for federated multi-repo storage and queries."""

    storage_backend: str = Field(
        default="sqlite",
        description="Storage backend: 'sqlite' (local ATTACH) or 'postgres' (enterprise server)",
    )
    postgres_url: str | None = Field(
        default=None,
        description="Connection URL when storage_backend is 'postgres'",
    )


class ProjectManifest(BaseModel):
    """Root configuration manifest for a federated multi-repo project workspace."""

    name: str = Field(..., min_length=1, max_length=100, description="Project workspace name")
    version: str = Field(default="1.0.0", description="Semantic version of project manifest")
    description: str = Field(default="", description="High-level description of project workspace")
    settings: ProjectSettings = Field(default_factory=ProjectSettings)
    members: list[RepoMember] = Field(
        default_factory=list, description="Registered member repositories"
    )


class FederatedNode(BaseModel):
    """AST node discovered across federated repositories."""

    repo: str
    id: str
    name: str
    qualified_name: str
    kind: str
    file_path: str
    start_line: int
    end_line: int
    signature: str | None = None


class FederatedCaller(BaseModel):
    """Caller referencing a target symbol across repositories."""

    calling_repo: str
    caller_symbol: str
    caller_file: str
    call_line: int | None = None
    caller_kind: str = "function"


class FederatedGuardReport(BaseModel):
    """Cross-repository blast radius guard report for a modified symbol."""

    target_symbol: str
    target_repo: str
    target_file: str
    total_callers: int
    callers_by_repo: dict[str, list[dict[str, Any]]] = Field(default_factory=dict)
    risk_level: str = "LOW"
    safety_advisory: str = ""


class MemberStatus(BaseModel):
    """Health and index status of a member repository."""

    name: str
    path: str
    resolved_path: str
    is_indexed: bool
    node_count: int = 0
    edge_count: int = 0
    db_size_bytes: int = 0
    last_modified: str | None = None
