"""Abstract base protocol and interface for federated multi-repository storage backends."""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from vibetrace.federation.models import (
    FederatedGuardReport,
    FederatedNode,
    MemberStatus,
)


@runtime_checkable
class StorageBackend(Protocol):
    """Protocol defining storage operations for multi-repo federation."""

    def close(self) -> None:
        """Release active database connections and resources. Must be idempotent and not raise."""
        ...

    def get_member_statuses(self) -> list[MemberStatus]:
        """Inspect and return current indexing and size status for all configured members."""
        ...

    def search(
        self,
        query: str,
        repo: str | None = None,
        limit: int = 20,
    ) -> list[FederatedNode]:
        """Perform symbol name and prefix search across one or all member repositories."""
        ...

    def get_cross_repo_guard(
        self,
        symbol_name: str,
        target_repo: str | None = None,
    ) -> FederatedGuardReport:
        """Trace callers of a symbol across all attached member repositories."""
        ...
