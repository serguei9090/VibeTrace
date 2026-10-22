"""Audit verification tests for Round 13 code critique fixes.

Covers:
1. Whitespace-only docstring does not crash format_semantic_search_md.
2. vibetrace_reindex_project(path=".") indexes the resolved project root, not server cwd.
3. GraphTraverser visited-set is cleared before each pass across multiple start nodes.
4. compute_impact_analysis uses removeprefix("./") so dot-directory paths stay intact.
"""

from __future__ import annotations

from pathlib import Path

from vibetrace.indexer.db import Edge, IndexerDB, Node
from vibetrace.indexer.parsers import ParserFactory
from vibetrace.mcp.formatters import format_semantic_search_md
from vibetrace.mcp.schemas import CallStackInput, ReindexInput, TraceDirection
from vibetrace.mcp.server import vibetrace_get_call_stack, vibetrace_reindex_project
from vibetrace.services.graph_service import compute_impact_analysis
from vibetrace.services.hybrid_search import hybrid_search


def test_whitespace_only_docstring_does_not_crash_search_formatter(tmp_path: Path):
    db = IndexerDB(str(tmp_path / "doc.db"))
    parser = ParserFactory.get_parser("w.py")
    assert parser is not None
    nodes, edges = parser.extract("w.py", b'def blank():\n    """   \n  """\n    pass\n')
    db.batch_save_file_index("w.py", "h", nodes, edges)

    hits = hybrid_search(db, "blank")
    assert hits
    out = format_semantic_search_md("blank", hits)
    assert "blank" in out


async def test_mcp_reindex_project_dot_uses_project_root_not_cwd(monkeypatch, tmp_path: Path):
    proj_a = tmp_path / "A"
    proj_b = tmp_path / "B"
    proj_a.mkdir()
    proj_b.mkdir()
    (proj_a / "a_mod.py").write_text("def a(): pass\n", encoding="utf-8")
    (proj_b / "b_mod.py").write_text("def b(): pass\n", encoding="utf-8")

    monkeypatch.setenv("VIBETRACE_PROJECT_ROOT", str(proj_a))
    monkeypatch.chdir(proj_b)

    await vibetrace_reindex_project(ReindexInput(path=".", force=True))
    db = IndexerDB(str(proj_a / "vibetrace_context" / "vibetrace.db"))
    indexed = db.get_all_indexed_file_paths()
    assert "a_mod.py" in indexed
    assert not any("b_mod.py" in p for p in indexed)


async def test_call_stack_clears_visited_between_start_nodes(monkeypatch, tmp_path: Path):
    db = IndexerDB(str(tmp_path / "cs.db"))

    def mk(nid: str, name: str, fp: str) -> Node:
        return Node(
            id=nid,
            name=name,
            qualified_name=name,
            kind="function",
            file_path=fp,
            start_line=1,
            end_line=2,
        )

    for n in (
        mk("r1", "run", "a.py"),
        mk("r2", "run", "b.py"),
        mk("h", "helper", "h.py"),
        mk("deep", "deep", "h.py"),
    ):
        db.upsert_node(n)
    for src, dst in (("r1", "h"), ("r2", "h"), ("h", "deep")):
        db.upsert_edge(Edge(from_node_id=src, to_node_id=dst, relation_type="calls", call_line=1))

    monkeypatch.setattr(
        "vibetrace.mcp.server._get_context_for_path",
        lambda *_a, **_k: (db, tmp_path),
    )
    out = await vibetrace_get_call_stack(
        CallStackInput(node_name="run", direction=TraceDirection.DOWN, depth=3)
    )
    assert out.count("run > helper > deep") == 2
    assert "already traversed in another branch" not in out


def test_compute_impact_analysis_preserves_dot_directory_paths(tmp_path: Path):
    db = IndexerDB(str(tmp_path / "imp.db"))
    db.upsert_node(
        Node(
            id="g1",
            name="ci_helper",
            qualified_name="ci_helper",
            kind="function",
            file_path=".github/workflows/ci.py",
            start_line=1,
            end_line=2,
        )
    )
    db.upsert_node(
        Node(
            id="c1",
            name="caller",
            qualified_name="caller",
            kind="function",
            file_path="src/runner.py",
            start_line=1,
            end_line=2,
        )
    )
    db.upsert_edge(Edge(from_node_id="c1", to_node_id="g1", relation_type="calls", call_line=1))

    total, levels = compute_impact_analysis(db, ".github/workflows/ci.py")
    assert total == 1
    assert any("caller" in item for item in levels[1])
