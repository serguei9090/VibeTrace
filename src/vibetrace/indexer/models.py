"""Domain data models for nodes and edges in the VibeTrace code graph."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel


class Node(BaseModel):
    id: str
    name: str
    qualified_name: str | None = None
    parent_id: str | None = None
    kind: Literal[
        "function",
        "class",
        "module",
        "interface",
        "variable",
        "struct",
        "trait",
        "impl",
        "method",
        "endpoint",
        "enum",
    ]
    file_path: str
    start_line: int | None = None
    end_line: int | None = None
    signature: str | None = None
    docstring: str | None = None
    decorators: list[str] | None = None
    visibility: str | None = None
    centrality: float = 0.0


class Edge(BaseModel):
    from_node_id: str
    to_node_id: str
    relation_type: Literal[
        "calls",
        "defines",
        "inherits",
        "references",
        "imports",
        "implements",
        "returns",
        "routes",
    ]
    call_line: int | None = None
