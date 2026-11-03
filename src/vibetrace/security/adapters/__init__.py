"""Security toolchain adapters for external scanning engines (Trivy, SARIF)."""

from __future__ import annotations

from vibetrace.security.adapters.trivy import TrivyAdapter

__all__ = ["TrivyAdapter"]
