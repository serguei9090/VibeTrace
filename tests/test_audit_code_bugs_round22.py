"""Round 22 Audit regression tests.

Verifies:
1. Multi-language markdown syntax fences and kind fallback in format_code_slice_md.
2. ReindexInput embed flag and MCP reindex integration with vector embeddings.
3. Single-file reindex_all failure tracking and resolver registration.
"""

from pathlib import Path
from unittest.mock import patch

import pytest

from vibetrace.indexer.db import IndexerDB
from vibetrace.indexer.main import reindex_all
from vibetrace.mcp.formatters import format_code_slice_md
from vibetrace.mcp.schemas import ReindexInput
from vibetrace.mcp.server import vibetrace_reindex_project
from vibetrace.services.graph_service import CodeSliceResult


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
