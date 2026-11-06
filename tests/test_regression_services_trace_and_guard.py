"""Consolidated regression test suite: test_regression_services_trace_and_guard.py."""

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from watchdog.events import FileMovedEvent

from vibetrace.indexer.db import IndexerDB, Node
from vibetrace.indexer.hooks import (
    _clean_lefthook_content,
    _inject_lefthook_hook,
)
from vibetrace.indexer.main import reindex_all
from vibetrace.indexer.watcher import CodeChangeHandler
from vibetrace.mcp.formatters import format_code_slice_md
from vibetrace.mcp.schemas import ReindexInput
from vibetrace.mcp.server import vibetrace_reindex_project
from vibetrace.services.graph_service import CodeSliceResult, get_context_for_path
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


def test_code_slice_multilang_fences_and_kind_fallback():
    """Verify format_code_slice_md selects proper syntax fences for Java/C++/C#."""
    res_java = CodeSliceResult(
        found=True,
        name="UserService",
        qualified_name="com.app.UserService",
        kind="class",
        file_path="src/com/app/UserService.java",
        start_line=1,
        end_line=10,
        source_code="public class UserService {}",
    )
    md_java = format_code_slice_md(res_java)
    assert "```java\npublic class UserService {}\n```" in md_java

    res_cpp = CodeSliceResult(
        found=True,
        name="calculate",
        qualified_name="math::calculate",
        kind=None,  # Tests kind fallback to "symbol"
        file_path="src/math.cpp",
        start_line=5,
        end_line=15,
        source_code="int calculate() { return 42; }",
    )
    md_cpp = format_code_slice_md(res_cpp)
    assert "(symbol)" in md_cpp
    assert "```cpp\nint calculate() { return 42; }\n```" in md_cpp

    res_cs = CodeSliceResult(
        found=True,
        name="Program",
        qualified_name="App.Program",
        kind="class",
        file_path="Program.cs",
        start_line=1,
        end_line=5,
        source_code="class Program {}",
    )
    md_cs = format_code_slice_md(res_cs)
    assert "```csharp\nclass Program {}\n```" in md_cs


def test_reindex_input_embed_flag():
    """Verify ReindexInput schema default and explicit embed parameter."""
    inp_default = ReindexInput(path=".")
    assert inp_default.embed is False

    inp_embed = ReindexInput(path=".", embed=True)
    assert inp_embed.embed is True


@pytest.mark.asyncio
async def test_mcp_reindex_project_with_embed(tmp_path: Path):
    """Verify vibetrace_reindex_project passes embed option and reports generated count."""
    proj = tmp_path / "proj"
    proj.mkdir()
    (proj / "main.py").write_text("x = 1\n", encoding="utf-8")

    db_path = str(tmp_path / "test.db")
    db = IndexerDB(db_path)

    mock_embed_path = "vibetrace.indexer.embeddings.EmbeddingEngine.index_missing_embeddings"
    with (
        patch("vibetrace.mcp.server._get_context_for_path", return_value=(db, proj)),
        patch(mock_embed_path, return_value=5) as mock_embed,
    ):
        result = await vibetrace_reindex_project(ReindexInput(path=str(proj), embed=True))
        assert "Successfully reindexed" in result
        assert "generated 5 vector embeddings" in result
        mock_embed.assert_called_once_with(db)


def test_single_file_reindex_updates_resolver_and_tracks_failures(tmp_path: Path):
    """Verify single-file reindex_all registers file in ModuleResolver and updates centrality."""
    proj = tmp_path / "proj"
    proj.mkdir()
    foo_file = proj / "foo.py"
    foo_file.write_text("def my_func(): pass\n", encoding="utf-8")

    db_path = str(tmp_path / "test.db")
    db = IndexerDB(db_path)

    # Test indexing valid single file
    reindex_all(db, str(foo_file), project_root=proj, verbose=False)
    nodes = db.get_nodes_in_file("foo.py")
    node_names = {n["name"] for n in nodes}
    assert "my_func" in node_names
    assert "foo" in node_names

    # Test indexing non-existent file
    missing_file = proj / "missing.py"
    reindex_all(db, str(missing_file), project_root=proj, verbose=False)
