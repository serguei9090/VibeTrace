"""Data models and contracts for VibeTrace Code Quality & Maintainability Analysis."""

from __future__ import annotations

from enum import Enum
from typing import Any

from pydantic import BaseModel, Field


class QualitySeverity(str, Enum):
    """Quality finding severity tier mapped to SonarQube impact."""

    HIGH = "HIGH"
    MEDIUM = "MEDIUM"
    LOW = "LOW"
    INFO = "INFO"


class QualityCategory(str, Enum):
    """Categorization of the detected quality defect or code smell."""

    COMPLEXITY = "complexity"
    CODE_SMELL = "code_smell"
    MAINTAINABILITY = "maintainability"
    RELIABILITY = "reliability"
    ACCESSIBILITY = "accessibility"


class QualityFinding(BaseModel):
    """Represents a discrete quality finding (code smell or complexity violation)."""

    id: str = Field(description="Unique fingerprint of this finding (e.g. QUAL-S3776-<hash>)")
    rule_id: str = Field(
        description="Standardized rule identifier (e.g. python:S3776, python:S1066)"
    )
    rspec_id: str = Field(description="SonarSource RSPEC ID (e.g. RSPEC-3776, RSPEC-1066)")
    category: QualityCategory
    severity: QualitySeverity
    title: str = Field(description="Short human-readable title of the quality issue")
    message: str = Field(description="Concise description of the violation")
    file_path: str = Field(description="Relative path to the affected source file")
    start_line: int = Field(default=1, description="1-indexed line where issue starts")
    end_line: int = Field(default=1, description="1-indexed line where issue ends")
    symbol_name: str | None = Field(default=None, description="Enclosing function or class symbol")
    code_hash: str = Field(
        description="Normalized SHA-256 hash of the enclosing symbol or code slice"
    )
    metric_value: float | None = Field(
        default=None, description="Measured metric value (e.g. complexity 22)"
    )
    metric_threshold: float | None = Field(default=None, description="Allowed threshold (e.g. 15)")
    non_compliant_snippet: str | None = Field(default=None, description="Offending code snippet")
    compliant_snippet: str | None = Field(default=None, description="Remediated compliant pattern")
    remediation_guidance: str | None = Field(
        default=None, description="SonarQube clean code guidance"
    )
    status: str = Field(default="UNAPPROVED", description="UNAPPROVED | APPROVED | MODIFIED_CODE")
    tag: str | None = Field(
        default=None, description="Acceptance tag: intentional | legacy | perf-critical"
    )
    rationale: str | None = Field(
        default=None, description="User explanation if approved as baseline exception"
    )
    reviewed_by: str | None = Field(default=None)
    reviewed_at: str | None = Field(default=None)
    metadata: dict[str, Any] = Field(default_factory=dict, description="Additional context")


class QualityAuditReport(BaseModel):
    """Aggregated report of code quality and complexity metrics across the project."""

    total_findings: int = 0
    high_count: int = 0
    medium_count: int = 0
    low_count: int = 0
    unapproved_count: int = 0
    modified_count: int = 0
    approved_count: int = 0
    average_complexity: float = 0.0
    max_complexity: int = 0
    max_complexity_symbol: str | None = None
    findings: list[QualityFinding] = Field(default_factory=list)


class QualityExceptionRecord(BaseModel):
    """Encapsulates approved exception details for .vibetrace/quality.toml."""

    id: str = ""
    finding_id: str | None = None
    rule_id: str
    file_path: str
    code_hash: str
    rationale: str
    severity: str = "HIGH"
    status: str = "APPROVED"
    reviewed_by: str = "cli-user"
    category: str = "complexity"
    symbol_name: str | None = None
    tag: str = "intentional"
    metric_value: float | None = None
    metric_threshold: float | None = None

    def model_post_init(self, __context: Any) -> None:
        if not self.id and self.finding_id:
            self.id = self.finding_id
        elif not self.finding_id and self.id:
            self.finding_id = self.id

    @classmethod
    def from_finding(
        cls,
        finding: QualityFinding,
        rationale: str,
        tag: str = "intentional",
        reviewed_by: str = "cli-user",
        status: str = "APPROVED",
    ) -> QualityExceptionRecord:
        """Constructs an exception record directly from a QualityFinding instance."""
        return cls(
            id=finding.id,
            rule_id=finding.rule_id,
            file_path=finding.file_path,
            code_hash=finding.code_hash,
            rationale=rationale,
            severity=str(getattr(finding.severity, "value", finding.severity)),
            status=status,
            reviewed_by=reviewed_by,
            category=str(getattr(finding.category, "value", finding.category)),
            symbol_name=finding.symbol_name,
            tag=tag,
            metric_value=finding.metric_value,
            metric_threshold=finding.metric_threshold,
        )
