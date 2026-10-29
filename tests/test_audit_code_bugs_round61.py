"""Tests for Round 61: Multi-Repository Project Workspace Federation.

1. Project manifest lifecycle: init, load, save, and auto-discovery.
2. Member repository lifecycle: add, remove, and status queries.
3. Federated SQLite engine: ATTACH DATABASE and repository-scoped search.
4. Cross-repository caller blast-radius guard.
5. CLI end-to-end execution: init, list, search, and guard with JSON output.
"""

from pathlib import Path

from vibetrace.cli import main as cli_main
from vibetrace.federation.config import (
    auto_discover_members,
    find_project_root,
    load_project_manifest,
    save_project_manifest,
)
from vibetrace.federation.models import ProjectManifest, ProjectSettings
from vibetrace.federation.project import ProjectWorkspace
from vibetrace.indexer.db import IndexerDB
from vibetrace.indexer.models import Edge, Node


def setup_mock_project(tmp_path: Path):
    """Create a mock multi-repo project workspace with two member repos."""
    project_root = tmp_path / "multi_suite"
    project_root.mkdir()

    repo_front = project_root / "frontend"
    repo_back = project_root / "backend"
    repo_front.mkdir()
    repo_back.mkdir()

    # Create package markers for auto-discovery
    (repo_front / "package.json").write_text('{"name": "frontend"}', encoding="utf-8")
    (repo_back / "pyproject.toml").write_text('[project]\nname = "backend"', encoding="utf-8")

    # Setup frontend database
    front_db_dir = repo_front / ".vibetrace"
    front_db_dir.mkdir()
    front_db = IndexerDB(str(front_db_dir / "vibetrace.db"))

    front_node = Node(
        id="fe_checkout_btn",
        name="onCheckoutClick",
        qualified_name="frontend.components.onCheckoutClick",
        kind="function",
        file_path="src/components/Checkout.tsx",
        start_line=10,
        end_line=30,
    )
    front_db.upsert_node(front_node)

    # Setup backend database
    back_db_dir = repo_back / ".vibetrace"
    back_db_dir.mkdir()
    back_db = IndexerDB(str(back_db_dir / "vibetrace.db"))

    back_node = Node(
        id="be_process_payment",
        name="process_payment",
        qualified_name="backend.services.process_payment",
        kind="function",
        file_path="src/services/payment.py",
        start_line=25,
        end_line=50,
    )
    back_caller = Node(
        id="be_api_route",
        name="post_checkout_order",
        qualified_name="backend.routes.post_checkout_order",
        kind="function",
        file_path="src/routes/order.py",
        start_line=15,
        end_line=40,
    )
    back_db.upsert_node(back_node)
    back_db.upsert_node(back_caller)

    # Add caller in backend calling process_payment
    back_db.upsert_edge(
        Edge(
            from_node_id="be_api_route",
            to_node_id="be_process_payment",
            relation_type="calls",
            call_line=20,
        )
    )

    # Add cross-repo caller in frontend calling process_payment
    front_db.upsert_edge(
        Edge(
            from_node_id="fe_checkout_btn",
            to_node_id="process_payment",
            relation_type="calls",
            call_line=18,
        )
    )

    return project_root, repo_front, repo_back


def test_manifest_lifecycle_and_discovery(tmp_path: Path):
    """Verify manifest creation, serialization, finding root, and member auto-discovery."""
    proj_root, repo_front, _ = setup_mock_project(tmp_path)

    # 1. Auto-discover candidate members
    candidates = auto_discover_members(proj_root)
    assert len(candidates) == 2
    names = {c.name for c in candidates}
    assert "frontend" in names
    assert "backend" in names

    # 2. Save manifest
    manifest = ProjectManifest(
        name="E-Commerce Suite",
        description="Federated microservices",
        settings=ProjectSettings(storage_backend="sqlite"),
        members=candidates,
    )
    save_project_manifest(proj_root, manifest)

    # 3. Find project root from a child directory
    found_root = find_project_root(repo_front / "src")
    assert found_root == proj_root

    # 4. Load manifest and verify contents
    loaded = load_project_manifest(proj_root)
    assert loaded.name == "E-Commerce Suite"
    assert len(loaded.members) == 2
    assert loaded.settings.storage_backend == "sqlite"


def test_project_workspace_member_management(tmp_path: Path):
    """Verify ProjectWorkspace init, member adding, removing, and status."""
    proj_root, _, _ = setup_mock_project(tmp_path)

    # Initialize workspace
    ws = ProjectWorkspace.init(proj_root, name="TestSuite", auto_discover=True)
    assert len(ws.manifest.members) == 2

    # Check member statuses
    statuses = ws.list_members()
    assert len(statuses) == 2
    stat_map = {s.name: s for s in statuses}
    assert stat_map["frontend"].is_indexed is True
    assert stat_map["frontend"].node_count >= 1
    assert stat_map["backend"].is_indexed is True
    assert stat_map["backend"].node_count >= 2

    # Add a third member
    new_dir = proj_root / "shared"
    new_dir.mkdir()
    member = ws.add_member("shared", "shared", description="Shared schemas")
    assert member.name == "shared"
    assert len(ws.manifest.members) == 3

    # Remove the member
    removed = ws.remove_member("shared")
    assert removed is True
    assert len(ws.manifest.members) == 2
    ws.close()


def test_federated_sqlite_search(tmp_path: Path):
    """Verify federated search across all repositories and repository-scoped search."""
    proj_root, _, _ = setup_mock_project(tmp_path)
    ws = ProjectWorkspace.init(proj_root, name="SearchSuite", auto_discover=True)

    # 1. Search across all repos (unscoped)
    all_results = ws.search("payment")
    assert len(all_results) >= 1
    assert all_results[0].repo == "backend"
    assert all_results[0].name == "process_payment"

    # 2. Search scoped to frontend
    fe_results = ws.search("Checkout", repo="frontend")
    assert len(fe_results) >= 1
    assert fe_results[0].repo == "frontend"
    assert fe_results[0].name == "onCheckoutClick"

    # 3. Search scoped to backend for a frontend-only symbol (returns empty)
    no_results = ws.search("onCheckoutClick", repo="backend")
    assert len(no_results) == 0
    ws.close()


def test_cross_repo_caller_guard(tmp_path: Path):
    """Verify cross-repository blast-radius detection across member repositories."""
    proj_root, _, _ = setup_mock_project(tmp_path)
    ws = ProjectWorkspace.init(proj_root, name="GuardSuite", auto_discover=True)

    # Inspect process_payment: defined in backend, called in both backend and frontend
    report = ws.guard("process_payment")
    assert report.target_symbol == "process_payment"
    assert report.target_repo == "backend"
    assert report.total_callers == 2

    # Check callers by repository
    assert "backend" in report.callers_by_repo
    assert "frontend" in report.callers_by_repo

    be_callers = report.callers_by_repo["backend"]
    assert any(c["caller_symbol"] == "post_checkout_order" for c in be_callers)

    fe_callers = report.callers_by_repo["frontend"]
    assert any(c["caller_symbol"] == "onCheckoutClick" for c in fe_callers)

    assert "Cross-repo blast radius: 2 caller(s) across 2 repository(ies)" in report.safety_advisory
    ws.close()


def test_project_cli_commands(tmp_path: Path, capsys):
    """Verify vibetrace project CLI subcommands with JSON output."""
    proj_root, _, _ = setup_mock_project(tmp_path)

    # 1. project init
    code_init = cli_main(
        ["--root", str(proj_root), "--json", "project", "init", "--name", "CliSuite"]
    )
    assert code_init == 0
    captured_init = capsys.readouterr()
    assert '"name": "CliSuite"' in captured_init.out

    # 2. project list
    code_list = cli_main(["--root", str(proj_root), "--json", "project", "list"])
    assert code_list == 0
    captured_list = capsys.readouterr()
    assert '"name": "frontend"' in captured_list.out
    assert '"name": "backend"' in captured_list.out

    # 3. project search
    code_search = cli_main(["--root", str(proj_root), "--json", "project", "search", "payment"])
    assert code_search == 0
    captured_search = capsys.readouterr()
    assert '"repo": "backend"' in captured_search.out
    assert '"name": "process_payment"' in captured_search.out

    # 4. project guard
    code_guard = cli_main(
        ["--root", str(proj_root), "--json", "project", "guard", "process_payment"]
    )
    assert code_guard == 0
    captured_guard = capsys.readouterr()
    assert '"total_callers": 2' in captured_guard.out
    assert '"target_repo": "backend"' in captured_guard.out
