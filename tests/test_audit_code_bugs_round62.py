"""Tests for Round 62: Phase 1B Cross-Repo Contract Linker & Postgres Storage Adapter.

1. Cross-repo contract linking between frontend client fetch and backend route handlers.
2. Cross-repo caller guard enrichment via synthesized HTTP contract links.
3. PostgreSQL storage backend adapter schema generation and query execution.
4. CLI `vibetrace project links` execution with JSON formatting.
"""

from pathlib import Path
from unittest.mock import MagicMock

from vibetrace.cli import main as cli_main
from vibetrace.federation.models import RepoMember
from vibetrace.federation.project import ProjectWorkspace
from vibetrace.federation.storage_postgres import POSTGRES_SCHEMA_SQL, PostgresStorageBackend
from vibetrace.indexer.db import IndexerDB
from vibetrace.indexer.models import Edge, Node


def setup_linked_workspace(tmp_path: Path):
    """Create a mock workspace with client and server route contracts."""
    project_root = tmp_path / "linked_suite"
    project_root.mkdir()

    fe_dir = project_root / "frontend"
    be_dir = project_root / "backend"
    fe_dir.mkdir()
    be_dir.mkdir()

    (fe_dir / "package.json").write_text('{"name": "frontend"}', encoding="utf-8")
    (be_dir / "pyproject.toml").write_text('[project]\nname = "backend"', encoding="utf-8")

    # Frontend DB
    fe_db_dir = fe_dir / ".vibetrace"
    fe_db_dir.mkdir()
    fe_db = IndexerDB(str(fe_db_dir / "vibetrace.db"))

    fe_fn = Node(
        id="fe_pay_btn",
        name="onPaymentSubmit",
        qualified_name="frontend.pay.onPaymentSubmit",
        kind="function",
        file_path="src/pay.ts",
        start_line=12,
        end_line=28,
    )
    fe_endpoint = Node(
        id="fe_ep_checkout",
        name="POST /api/v1/checkout",
        qualified_name="endpoint:POST /api/v1/checkout",
        kind="endpoint",
        file_path="src/pay.ts",
        start_line=18,
        end_line=18,
    )
    fe_db.upsert_node(fe_fn)
    fe_db.upsert_node(fe_endpoint)
    fe_db.upsert_edge(
        Edge(
            from_node_id="fe_pay_btn",
            to_node_id="fe_ep_checkout",
            relation_type="calls",
            call_line=18,
        )
    )

    # Backend DB
    be_db_dir = be_dir / ".vibetrace"
    be_db_dir.mkdir()
    be_db = IndexerDB(str(be_db_dir / "vibetrace.db"))

    be_endpoint = Node(
        id="be_ep_checkout",
        name="POST /api/v1/checkout",
        qualified_name="endpoint:POST /api/v1/checkout",
        kind="endpoint",
        file_path="src/routes.py",
        start_line=10,
        end_line=10,
    )
    be_handler = Node(
        id="be_handler_checkout",
        name="handle_checkout",
        qualified_name="backend.routes.handle_checkout",
        kind="function",
        file_path="src/routes.py",
        start_line=11,
        end_line=45,
    )
    be_db.upsert_node(be_endpoint)
    be_db.upsert_node(be_handler)
    be_db.upsert_edge(
        Edge(
            from_node_id="be_ep_checkout",
            to_node_id="be_handler_checkout",
            relation_type="routes",
            call_line=10,
        )
    )

    return project_root, fe_dir, be_dir


def test_cross_repo_contract_discovery(tmp_path: Path):
    """Verify HTTP client calls match server route handlers across repos."""
    project_root, _, _ = setup_linked_workspace(tmp_path)
    ws = ProjectWorkspace.init(project_root, name="LinkedProject", auto_discover=True)

    links = ws.discover_cross_repo_links()
    assert len(links) == 1
    link = links[0]
    assert link.source_repo == "frontend"
    assert link.source_symbol == "onPaymentSubmit"
    assert link.target_repo == "backend"
    assert link.target_symbol == "handle_checkout"
    assert link.contract_key == "POST /api/v1/checkout"
    assert link.protocol == "http"
    ws.close()


def test_cross_repo_guard_with_contract_links(tmp_path: Path):
    """Verify modifying a backend route handler detects client-side callers in frontend."""
    project_root, _, _ = setup_linked_workspace(tmp_path)
    ws = ProjectWorkspace.init(project_root, name="LinkedGuard", auto_discover=True)

    report = ws.guard("handle_checkout")
    assert report.target_symbol == "handle_checkout"
    assert report.target_repo == "backend"
    assert report.total_callers >= 1

    assert "frontend" in report.callers_by_repo
    fe_callers = report.callers_by_repo["frontend"]
    assert any("onPaymentSubmit" in c["caller_symbol"] for c in fe_callers)
    ws.close()


def test_postgres_storage_adapter():
    """Verify PostgreSQL storage adapter DDL generation and parameterized query behavior."""
    assert "CREATE TABLE IF NOT EXISTS vibetrace_projects" in POSTGRES_SCHEMA_SQL
    assert "CREATE TABLE IF NOT EXISTS vibetrace_nodes" in POSTGRES_SCHEMA_SQL
    assert "CREATE TABLE IF NOT EXISTS vibetrace_edges" in POSTGRES_SCHEMA_SQL

    mock_conn = MagicMock()
    mock_cursor = MagicMock()
    mock_conn.cursor.return_value.__enter__.return_value = mock_cursor

    mock_cursor.fetchone.return_value = ("backend", "node_123", "src/pay.py")
    mock_cursor.fetchall.return_value = [
        ("frontend", 15, "onPayClick", "src/app.tsx", "function"),
    ]

    backend = PostgresStorageBackend(
        postgres_url="postgres://user:pass@localhost:5432/vibe",
        project_id="test_proj",
        members=[RepoMember(name="frontend", path="frontend")],
        connection=mock_conn,
    )

    # 1. Test init schema
    backend.init_schema()
    mock_cursor.execute.assert_called_with(POSTGRES_SCHEMA_SQL)
    mock_conn.commit.assert_called_once()

    # 2. Test guard query
    report = backend.get_cross_repo_guard("process_payment")
    assert report.target_symbol == "process_payment"
    assert report.total_callers == 1
    assert "frontend" in report.callers_by_repo

    # 3. Test search query
    mock_cursor.fetchall.return_value = [
        (
            "backend",
            "node_1",
            "process_payment",
            "backend.pay",
            "function",
            "src/pay.py",
            1,
            10,
            None,
        )
    ]
    results = backend.search("process")
    assert len(results) == 1
    assert results[0].name == "process_payment"


def test_cli_project_links(tmp_path: Path, capsys):
    """Verify `vibetrace project links --json` CLI command."""
    project_root, _, _ = setup_linked_workspace(tmp_path)

    cli_main(["--root", str(project_root), "--json", "project", "init", "--name", "LinksSuite"])
    capsys.readouterr()

    code = cli_main(["--root", str(project_root), "--json", "project", "links"])
    assert code == 0
    captured = capsys.readouterr()
    assert '"source_repo": "frontend"' in captured.out
    assert '"target_repo": "backend"' in captured.out
    assert '"contract_key": "POST /api/v1/checkout"' in captured.out
