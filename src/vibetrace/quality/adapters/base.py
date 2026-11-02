"""Base protocol and contracts for external linter & type-checker sub-engine adapters."""

from __future__ import annotations

from abc import ABC, abstractmethod
from pathlib import Path

from vibetrace.quality.models import QualityFinding


class LinterSubEngine(ABC):
    """Abstract interface implemented by native toolchain adapters (Ruff, Pyright, Biome)."""

    name: str

    @abstractmethod
    def is_available(self, project_root: Path) -> bool:
        """Returns True if the underlying tool binary or runner is executable in the workspace."""
        ...

    @abstractmethod
    def audit_file(self, file_path: Path, project_root: Path) -> list[QualityFinding]:
        """Audits a single file and returns standardized Sonar-mapped QualityFinding records."""
        ...

    @abstractmethod
    def audit_project(
        self,
        project_root: Path,
        file_list: list[Path] | None = None,
    ) -> list[QualityFinding]:
        """Audits target files or the entire project using batch-optimized sub-processes."""
        ...
