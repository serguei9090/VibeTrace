"""Dedicated tests for multi-repository workspace federation."""

from __future__ import annotations

from pathlib import Path

import pytest

from vibetrace.federation.models import ProjectManifest, ProjectSettings, RepoMember
from vibetrace.federation.project import ProjectWorkspace
from vibetrace.federation.storage import FederatedSQLiteEngine
from vibetrace.federation.storage_postgres import PostgresStorageBackend


def test_federation_workspace_lifecycle_and_member_management(tmp_path: Path) -> None:
    svc_dir = tmp_path / "svc_a"
    svc_dir.mkdir()
    (svc_dir / "pyproject.toml").write_text('[project]\nname = "svc_a"\n', encoding="utf-8")

    with ProjectWorkspace.init(tmp_path, name="PolyRepo", auto_discover=True) as ws:
        statuses = ws.list_members()
        assert [s.name for s in statuses] == ["svc_a"]
        assert statuses[0].is_indexed is False

        with pytest.raises(ValueError, match="already registered"):
            ws.add_member("svc_a", "svc_a")

        extra_dir = tmp_path / "svc_b"
        extra_dir.mkdir()
        ws.add_member("svc_b", "svc_b", description="Second repo")
        assert len(ws.list_members()) == 2

        assert ws.remove_member("svc_b") is True
        assert ws.remove_member("missing_repo") is False

    loaded = ProjectWorkspace.load(tmp_path)
    try:
        assert loaded.manifest.name == "PolyRepo"
        assert [m.name for m in loaded.manifest.members] == ["svc_a"]
    finally:
        loaded.close()


def test_federation_index_search_and_cross_repo_guard(tmp_path: Path) -> None:
    core_dir = tmp_path / "core_lib"
    api_dir = tmp_path / "api_svc"
    web_dir = tmp_path / "web_app"
    core_dir.mkdir()
    api_dir.mkdir()
    web_dir.mkdir()

    (core_dir / "billing.py").write_text(
        "def charge_card(amount: int) -> int:\n    return amount * 100\n\n"
        "def charge_card_legacy(amount: int) -> int:\n    return amount\n",
        encoding="utf-8",
    )
    (api_dir / "routes.py").write_text(
        "from core_lib.billing import charge_card\n\n"
        "def handle_checkout() -> int:\n    return charge_card(10)\n",
        encoding="utf-8",
    )
    (web_dir / "views.py").write_text(
        "from core_lib.billing import charge_card\n\n"
        "def pay_view() -> int:\n    return charge_card(5)\n",
        encoding="utf-8",
    )

    manifest = ProjectManifest(
        name="FedSuite",
        members=[
            RepoMember(name="core_lib", path="core_lib"),
            RepoMember(name="api_svc", path="api_svc"),
            RepoMember(name="web_app", path="web_app"),
        ],
    )
    with ProjectWorkspace(tmp_path, manifest) as ws:
        res_core = ws.index_member("core_lib", force=True)
        assert res_core["status"] == "indexed"
        assert res_core["node_count"] >= 2

        batch = ws.index_all(force=True)
        assert all(item["status"] == "indexed" for item in batch)

        hits = ws.search("charge_card", limit=10)
        assert len(hits) >= 2
        # Exact name matches rank in tier 0 ahead of substring/prefix matches
        assert hits[0].name == "charge_card"
        assert any(h.name == "charge_card_legacy" for h in hits)

        report = ws.guard("charge_card")
        assert report.total_callers >= 2
        assert "api_svc" in report.callers_by_repo
        assert "web_app" in report.callers_by_repo
        assert len(report.callers_by_repo) >= 2
        assert report.risk_level == "HIGH"
        assert "billing" not in [
            c["caller_symbol"] for c in report.callers_by_repo.get("core_lib", [])
        ]


def test_federation_index_member_rejects_path_escape(tmp_path: Path) -> None:
    proj_dir = tmp_path / "workspace"
    proj_dir.mkdir()
    manifest = ProjectManifest(
        name="EscapeCheck",
        members=[RepoMember(name="outside", path="../outside")],
    )
    with ProjectWorkspace(proj_dir, manifest) as ws:
        with pytest.raises(ValueError, match="escapes project root"):
            ws.index_member("outside")


def test_federation_engine_selects_sqlite_or_postgres_backend(tmp_path: Path) -> None:
    sqlite_manifest = ProjectManifest(
        name="SqliteProj",
        settings=ProjectSettings(storage_backend="sqlite"),
        members=[],
    )
    with ProjectWorkspace(tmp_path, sqlite_manifest) as ws_sqlite:
        assert isinstance(ws_sqlite.engine, FederatedSQLiteEngine)

    pg_manifest = ProjectManifest(
        name="PgProj",
        settings=ProjectSettings(
            storage_backend="postgres",
            postgres_url="postgresql://user:pass@localhost:5432/vibetrace",
        ),
        members=[],
    )
    with ProjectWorkspace(tmp_path, pg_manifest) as ws_pg:
        assert isinstance(ws_pg.engine, PostgresStorageBackend)
        assert ws_pg.engine.get_cross_repo_links() == []
