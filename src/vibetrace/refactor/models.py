"""Data contracts for AST-based method extraction and code refactoring."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any


@dataclass
class CandidateSplitBlock:
    """Candidate contiguous AST statement block suitable for extraction."""

    start_line: int
    end_line: int
    complexity_score: int
    complexity_reduction: int
    inputs: list[str]
    outputs: list[str]
    statement_types: list[str]
    summary: str

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class RefactorPlan:
    """Complete blueprint for extracting a statement block into a helper function."""

    target_function: str
    file_path: str
    start_line: int
    end_line: int
    helper_name: str
    helper_code: str
    call_replacement: str
    original_snippet: str
    refactored_full_source: str
    can_auto_apply: bool
    rejection_reason: str | None = None
    diff: str = ""
    inputs: list[str] = field(default_factory=list)
    outputs: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)
