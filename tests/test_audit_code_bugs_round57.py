"""Tests for Round 57: Upgrading call-stack, dependencies, and init to A+/S marks.

1. call-stack: Execution-only filtering by default and --all-relations.
2. dependencies: Bidirectional inbound dependents, circular import detection, and --reverse flag.
3. init: Ecosystem auto-detection (Python, JS/TS, Rust, Go) and doc catalog auto-init.
"""

from pathlib import Path

import pytest

from vibetrace.indexer.db import IndexerDB
from vibetrace.indexer.models import Edge, Node
from vibetrace.mcp.formatters import format_dependencies_md
from vibetrace.services.graph_service import (
    analyze_file_dependencies,
    build_call_stack,
)
from vibetrace.skill_installer import (
    detect_project_ecosystems,
    initialize_workspace,
)


@pytest.fixture
def test_db_env(tmp_path: Path):
    db_path = tmp_path / "test.db"
    db = IndexerDB(str(db_path))
    # Populate test nodes
    n1 = Node(
        id="node_caller",
        name="caller_fn",
        qualified_name="caller_fn",
        kind="function",
        file_path="src/caller.py",
        start_line=10,
        end_line=20,
        signature="() -> None",
    )
    n2 = Node(
        id="node_target",
        name="target_fn",
        qualified_name="target_fn",
        kind="function",
        file_path="src/target.py",
        start_line=30,
        end_line=40,
        signature="() -> None",
    )
    n3 = Node(
        id="node_callee",
        name="callee_fn",
        qualified_name="callee_fn",
        kind="function",
        file_path="src/callee.py",
        start_line=50,
        end_line=60,
        signature="() -> None",
    )
    n_module = Node(
        id="node_module",
        name="target_mod",
        qualified_name="src.target",
        kind="module",
        file_path="src/target.py",
        start_line=1,
        end_line=100,
    )
    db.upsert_node(n1)
    db.upsert_node(n2)
    db.upsert_node(n3)
    db.upsert_node(n_module)

    # Add edges:
    # 1. caller_fn calls target_fn (line 15)
    # 2. target_fn calls callee_fn (line 35)
    # 3. target_mod defines target_fn (line 30) - non-executable AST relation
    db.upsert_edge(
        Edge(
            from_node_id="node_caller",
            to_node_id="node_target",
            relation_type="calls",
            call_line=15,
        )
    )
    db.upsert_edge(
        Edge(
            from_node_id="node_target",
            to_node_id="node_callee",
            relation_type="calls",
            call_line=35,
        )
    )
    db.upsert_edge(
        Edge(
            from_node_id="node_module",
            to_node_id="node_target",
            relation_type="defines",
            call_line=None,
        )
    )
    return db


def test_call_stack_filtered_execution_and_line_numbers(test_db_env: IndexerDB):
    """Verify call-stack defaults to executable calls and includes line numbers."""
    db = test_db_env
    # Default: only executable ("calls", "routes")
    found, output, nodes = build_call_stack(db, "target_fn", direction="both")
    assert found is True
    assert len(nodes) == 1
    text = "\n".join(output)

    # Verify caller and callee are found with exact :L<line> numbers
    assert "caller_fn" in text
    assert "src/caller.py:L15" in text
    assert "callee_fn" in text
    assert "src/callee.py:L35" in text

    # Verify structural 'defines' relation is NOT present in default call stack
    assert "(defines)" not in text
    assert "target_mod" not in text

    # With relation_types=None (all-relations): 'defines' must be included
    found_all, output_all, _ = build_call_stack(
        db, "target_fn", direction="both", relation_types=None
    )
    assert found_all is True
    text_all = "\n".join(output_all)
    assert "(defines)" in text_all
    assert "target_mod" in text_all


def test_dependencies_reverse_dependents_and_circular(tmp_path: Path):
    """Verify bidirectional dependents and circular import detection."""
    db_path = tmp_path / "deps.db"
    db = IndexerDB(str(db_path))

    # File A (src/a.py) imports File B (src/b.py)
    # File B (src/b.py) imports File A (src/a.py) -> Circular import
    # File C (src/c.py) imports File A (src/a.py) -> Dependent of A
    for name, path in [("a", "src/a.py"), ("b", "src/b.py"), ("c", "src/c.py")]:
        db.upsert_node(
            Node(
                id=f"mod_{name}",
                name=name,
                qualified_name=name,
                kind="module",
                file_path=path,
                start_line=1,
                end_line=10,
            )
        )

    db.upsert_edge(
        Edge(
            from_node_id="mod_a",
            to_node_id="mod_b",
            relation_type="imports",
            call_line=1,
        )
    )
    db.upsert_edge(
        Edge(
            from_node_id="mod_b",
            to_node_id="mod_a",
            relation_type="imports",
            call_line=1,
        )
    )
    db.upsert_edge(
        Edge(
            from_node_id="mod_c",
            to_node_id="mod_a",
            relation_type="imports",
            call_line=1,
        )
    )

    analysis_a = analyze_file_dependencies(db, "src/a.py")
    assert len(analysis_a["internal"]) == 1
    assert analysis_a["internal"][0]["file_path"] == "src/b.py"

    # Inbound dependents: both src/b.py and src/c.py import src/a.py
    dep_files = [d["file_path"] for d in analysis_a["dependents"]]
    assert "src/b.py" in dep_files
    assert "src/c.py" in dep_files

    # Circular detection: mutual import loop between src/a.py and src/b.py
    assert len(analysis_a["circular"]) == 1
    assert "src/b.py" in analysis_a["circular"][0]["target_file"]

    # Markdown rendering check
    md_normal = format_dependencies_md(
        "src/a.py",
        analysis_a["internal"],
        analysis_a["third_party"],
        analysis_a["stdlib"],
        dependents=analysis_a["dependents"],
        circular=analysis_a["circular"],
        reverse_only=False,
    )
    assert "Circular Dependency Alert" in md_normal
    assert "Inbound Dependents (2 files import this module)" in md_normal

    md_rev = format_dependencies_md(
        "src/a.py",
        analysis_a["internal"],
        analysis_a["third_party"],
        analysis_a["stdlib"],
        dependents=analysis_a["dependents"],
        circular=analysis_a["circular"],
        reverse_only=True,
    )
    assert "Internal Project Modules" not in md_rev
    assert "Inbound Dependents" in md_rev


def test_init_ecosystem_detection_and_options(tmp_path: Path):
    """Verify ecosystem detection for polyglot runtimes and complete workspace initialization."""
    # Polyglot repo with uv Python and bun TS
    (tmp_path / "pyproject.toml").write_text("[project]\nname = 'test'\n", encoding="utf-8")
    (tmp_path / "uv.lock").write_text("", encoding="utf-8")
    (tmp_path / "package.json").write_text('{"name": "test-ui"}', encoding="utf-8")
    (tmp_path / "bun.lock").write_text("", encoding="utf-8")

    # Add docs/ folder
    docs_dir = tmp_path / "docs"
    docs_dir.mkdir()
    (docs_dir / "guide.md").write_text("# Guide\n\nContent here.", encoding="utf-8")

    ecosystems = detect_project_ecosystems(tmp_path)
    assert "Python (uv)" in ecosystems
    assert "JavaScript/TypeScript (bun)" in ecosystems

    # Initialize workspace
    res = initialize_workspace(tmp_path, no_index=False, configure_hooks=False)
    assert res["project_root"] == str(tmp_path.resolve())
    assert res["gitignore_updated"] is True
    assert res["doc_catalog_registered"] is True
    assert res["doc_chunks_indexed"] >= 1
    assert "Python (uv)" in res["ecosystems"]
    assert Path(res["db_path"]).exists()
