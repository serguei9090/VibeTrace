"""Tests verifying fixes for Round 3 strict code bug critique audit."""

from pathlib import Path
from unittest.mock import MagicMock

from watchdog.events import FileMovedEvent

from vibetrace.indexer.db import IndexerDB, Node
from vibetrace.indexer.hooks import (
    _clean_lefthook_content,
    _inject_lefthook_hook,
)
from vibetrace.indexer.watcher import CodeChangeHandler
from vibetrace.services.graph_service import get_context_for_path
from vibetrace.skill_installer import initialize_workspace


def test_watcher_ignores_wal_and_tmp_moves(tmp_path: Path):
    """Verify on_moved ignores moves between ignored files without triggering sync."""
    db = MagicMock(spec=IndexerDB)
    on_change = MagicMock()
    handler = CodeChangeHandler(db, on_change=on_change, root_path=str(tmp_path))

    # Move from .vibetrace/vibetrace.db-wal to .vibetrace/vibetrace.db
    wal_event = FileMovedEvent(
        str(tmp_path / ".vibetrace" / "vibetrace.db-wal"),
        str(tmp_path / ".vibetrace" / "vibetrace.db"),
    )
    handler.on_moved(wal_event)

    # Move from .git/index.lock to .git/index
    git_event = FileMovedEvent(
        str(tmp_path / ".git" / "index.lock"),
        str(tmp_path / ".git" / "index"),
    )
    handler.on_moved(git_event)

    # Must NOT call clear_file or trigger on_change
    assert db.clear_file.call_count == 0
    assert on_change.call_count == 0


def test_db_batch_save_chunking_large_deletions(tmp_path: Path):
    """Verify batch_save_file_index chunks deleted_node_ids to avoid SQLite variable limit."""
    db_file = tmp_path / "test.db"
    db = IndexerDB(str(db_file))

    file_path = "large_module.py"
    # Seed file with 1,200 initial symbols
    initial_nodes = [
        Node(
            id=f"sym_{i}",
            name=f"func_{i}",
            kind="function",
            file_path=file_path,
            start_line=i,
            end_line=i + 1,
        )
        for i in range(1200)
    ]
    db.batch_save_file_index(file_path, "hash1", initial_nodes, [])

    with db._get_conn() as conn:
        count = conn.execute(
            "SELECT COUNT(*) FROM nodes WHERE file_path = ?", (file_path,)
        ).fetchone()[0]
        assert count == 1200

    # Reindex with only 2 remaining nodes (1,198 nodes deleted)
    remaining_nodes = [
        Node(
            id="sym_0",
            name="func_0",
            kind="function",
            file_path=file_path,
            start_line=1,
            end_line=2,
        ),
        Node(
            id="sym_1",
            name="func_1",
            kind="function",
            file_path=file_path,
            start_line=3,
            end_line=4,
        ),
    ]
    # This must NOT throw sqlite3.OperationalError: too many SQL variables
    db.batch_save_file_index(file_path, "hash2", remaining_nodes, [])

    with db._get_conn() as conn:
        count = conn.execute(
            "SELECT COUNT(*) FROM nodes WHERE file_path = ?", (file_path,)
        ).fetchone()[0]
        assert count == 2


def test_lefthook_merges_into_existing_hook_section():
    """Verify _inject_lefthook_hook merges into existing post-commit commands without duplicates."""
    initial_yaml = """
post-commit:
  commands:
    custom-audit:
      run: npm audit
"""
    result = _inject_lefthook_hook(initial_yaml, "post-commit")

    # post-commit: must only appear once
    assert result.count("post-commit:") == 1
    assert "vibetrace-sync:" in result
    assert "custom-audit:" in result


def test_lefthook_uninstall_cleans_empty_commands():
    """Verify uninstalling VibeTrace prunes empty commands: blocks in lefthook.yml."""
    yaml_with_vibetrace = """
post-commit:
  commands:
    vibetrace-sync:
      run: uv run vibetrace index
"""
    cleaned = _clean_lefthook_content(yaml_with_vibetrace)
    # Both post-commit: and commands: should be removed, leaving empty/blank string
    assert "post-commit" not in cleaned
    assert "commands:" not in cleaned


def test_lefthook_uninstall_preserves_other_commands():
    """Verify uninstalling VibeTrace preserves sibling commands in lefthook.yml."""
    yaml_with_mixed = """
post-commit:
  commands:
    lint:
      run: ruff check
    vibetrace-sync:
      run: uv run vibetrace index
"""
    cleaned = _clean_lefthook_content(yaml_with_mixed)
    assert "post-commit:" in cleaned
    assert "commands:" in cleaned
    assert "lint:" in cleaned
    assert "vibetrace" not in cleaned


def test_skill_installer_aligns_db_path_with_services(tmp_path: Path):
    """Verify initialize_workspace creates DB in .vibetrace/ matching get_context_for_path."""
    res = initialize_workspace(tmp_path, no_index=True)
    expected_db_path = tmp_path / ".vibetrace" / "vibetrace.db"
    assert res["db_path"] == str(expected_db_path)

    # get_context_for_path must resolve to the identical database file
    db, root = get_context_for_path(str(tmp_path), auto_sync=False)
    assert Path(db.db_path).resolve() == expected_db_path.resolve()
    assert root.resolve() == tmp_path.resolve()
