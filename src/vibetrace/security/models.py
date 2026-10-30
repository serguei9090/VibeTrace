"""Data models and contracts for VibeTrace Security & Vulnerability Analysis."""

from __future__ import annotations

from enum import Enum
from typing import Any

from pydantic import BaseModel, Field


class Severity(str, Enum):
    """Vulnerability severity tier mapped to CVSS impact."""

    CRITICAL = "CRITICAL"
    HIGH = "HIGH"
    MEDIUM = "MEDIUM"
    LOW = "LOW"
    INFO = "INFO"


class FindingCategory(str, Enum):
    """Categorization of the detected security defect."""

    AST_PATTERN = "ast_pattern"
    TAINT_REACHABILITY = "taint_reachability"
    PACKAGE_CVE = "package_cve"


class SecurityFinding(BaseModel):
    """Represents a discrete security finding detected in source code or dependencies."""

    id: str = Field(description="Unique fingerprint of this finding (e.g. SEC-CWE-78-<hash>)")
    rule_id: str = Field(
        description="Standardized rule identifier (e.g. python:CWE-78, npm:CVE-2023-...)"
    )
    cwe_id: str = Field(description="Common Weakness Enumeration ID (e.g. CWE-78, CWE-89)")
    category: FindingCategory
    severity: Severity
    title: str = Field(description="Short human-readable title of the vulnerability")
    message: str = Field(description="Concise description of what is wrong")
    file_path: str = Field(description="Relative path to the affected source file or lockfile")
    start_line: int = Field(default=1, description="1-indexed line where vulnerability occurs")
    end_line: int = Field(default=1, description="1-indexed line where vulnerability ends")
    symbol_name: str | None = Field(default=None, description="Enclosing function or class symbol")
    code_hash: str = Field(
        description="Normalized SHA-256 hash of the enclosing symbol or code slice"
    )
    non_compliant_snippet: str | None = Field(default=None, description="Vulnerable code snippet")
    compliant_snippet: str | None = Field(
        default=None, description="Remediated secure code pattern"
    )
    remediation_guidance: str | None = Field(
        default=None, description="SonarQube-style architectural explanation"
    )
    status: str = Field(
        default="UNAPPROVED", description="UNAPPROVED | APPROVED | MODIFIED_CODE | RISK"
    )
    tag: str | None = Field(
        default=None,
        description=(
            "Disposition tag: no-alternative | risk-accepted | "
            "false-positive | compensating-control"
        ),
    )
    rationale: str | None = Field(
        default=None, description="User explanation if approved as baseline exception"
    )
    reviewed_by: str | None = Field(default=None)
    reviewed_at: str | None = Field(default=None)
    metadata: dict[str, Any] = Field(
        default_factory=dict, description="Additional context (e.g. CVSS score, package version)"
    )


class SecurityAuditReport(BaseModel):
    """Aggregated report of security findings across the project."""

    total_findings: int = 0
    critical_count: int = 0
    high_count: int = 0
    medium_count: int = 0
    low_count: int = 0
    unapproved_count: int = 0
    modified_count: int = 0
    approved_count: int = 0
    findings: list[SecurityFinding] = Field(default_factory=list)
