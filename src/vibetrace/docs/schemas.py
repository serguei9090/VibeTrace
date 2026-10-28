"""Documentation RAG schemas and data models."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field


class DocChunkItem(BaseModel):
    """Retrieved documentation chunk with location and matching score."""

    id: str
    catalog: str
    scope: Literal["local", "global"]
    file_path: str
    heading_path: str
    start_line: int
    end_line: int
    content: str
    score: float = 0.0
    match_sources: list[str] = Field(default_factory=list)


class DocCatalogInfo(BaseModel):
    """Metadata describing a documentation catalog."""

    name: str
    scope: Literal["local", "global"]
    paths: list[str]
    doc_count: int = 0
    chunk_count: int = 0


class DocSearchQuery(BaseModel):
    """Request model for documentation search."""

    query: str
    catalog: str | None = None
    scope: Literal["local", "global", "all"] = "local"
    limit: int = 5
    file_pattern: str | None = None
