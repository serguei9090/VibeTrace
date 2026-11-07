"""Multi-repository project workspace federation subsystem for VibeTrace."""

from vibetrace.federation.config import (
    PROJECT_CONFIG_FILENAME,
    auto_discover_members,
    find_project_root,
    load_project_manifest,
    save_project_manifest,
)
from vibetrace.federation.models import (
    FederatedCaller,
    FederatedGuardReport,
    FederatedNode,
    MemberStatus,
    ProjectManifest,
    ProjectSettings,
    RepoMember,
)
from vibetrace.federation.project import ProjectWorkspace
from vibetrace.federation.storage import FederatedSQLiteEngine
from vibetrace.federation.storage_base import StorageBackend

__all__ = [
    "PROJECT_CONFIG_FILENAME",
    "FederatedCaller",
    "FederatedGuardReport",
    "FederatedNode",
    "FederatedSQLiteEngine",
    "MemberStatus",
    "ProjectManifest",
    "ProjectSettings",
    "ProjectWorkspace",
    "RepoMember",
    "StorageBackend",
    "auto_discover_members",
    "find_project_root",
    "load_project_manifest",
    "save_project_manifest",
]
