"""Consolidated regression test suite: test_regression_services_docs_and_search.py."""

from __future__ import annotations

from pathlib import Path

import pytest

from vibetrace.indexer.db import IndexerDB
from vibetrace.indexer.models import Edge, Node
from vibetrace.mcp.formatters import format_dependencies_md
from vibetrace.services.call_tree_service import build_call_tree
from vibetrace.services.caller_guard import CallerGuardService, format_caller_guard_md
from vibetrace.services.graph_service import (
    analyze_file_dependencies,
    build_call_stack,
    compute_impact_analysis,
)
from vibetrace.services.trace_service import trace_execution_path
from vibetrace.services.vibe_bundle import VibeBundleService
from vibetrace.skill_installer import (
    detect_project_ecosystems,
    initialize_workspace,
)


def test_tracepath_disambiguation_with_identical_symbol_names(tmp_path: Path) -> None:
    """Verify trace_execution_path disambiguates identical symbol names across different files."""
    db_file = tmp_path / "disambig_test.db"
    db = IndexerDB(str(db_file))

    # Worker A in file_a.py
    node_a = Node(
        id="file_a.py:Worker.run:10",
        name="run",
        qualified_name="Worker.run",
        kind="function",
        file_path="src/file_a.py",
        start_line=10,
        end_line=15,
    )
    # Target A in file_a.py
    target_a = Node(
        id="file_a.py:save:20",
        name="save",
        qualified_name="save",
        kind="function",
        file_path="src/file_a.py",
        start_line=20,
        end_line=25,
    )

    # Worker B in file_b.py
    node_b = Node(
        id="file_b.py:Worker.run:10",
        name="run",
        qualified_name="Worker.run",
        kind="function",
        file_path="src/file_b.py",
        start_line=10,
        end_line=15,
    )
    # Target B in file_b.py
    target_b = Node(
        id="file_b.py:save:20",
        name="save",
        qualified_name="save",
        kind="function",
        file_path="src/file_b.py",
        start_line=20,
        end_line=25,
    )

    db.upsert_node(node_a)
    db.upsert_node(target_a)
    db.upsert_node(node_b)
    db.upsert_node(target_b)

    # Connect A -> target_a, B -> target_b
    db.upsert_edge(
        Edge(
            from_node_id=node_a.id,
            to_node_id=target_a.id,
            relation_type="calls",
            call_line=12,
        )
    )
    db.upsert_edge(
        Edge(
            from_node_id=node_b.id,
            to_node_id=target_b.id,
            relation_type="calls",
            call_line=12,
        )
    )

    # Trace specifically from file_b to file_b
    res_b = trace_execution_path(
        db,
        "run",
        "save",
        from_file="src/file_b.py",
        to_file="src/file_b.py",
    )
    assert res_b.found is True
    assert len(res_b.hops) == 1
    assert res_b.hops[0].from_node.file_path == "src/file_b.py"
    assert res_b.hops[0].to_node.file_path == "src/file_b.py"

    # Trace specifically from file_a to file_a
    res_a = trace_execution_path(
        db,
        "run",
        "save",
        from_file="src/file_a.py",
        to_file="src/file_a.py",
    )
    assert res_a.found is True
    assert len(res_a.hops) == 1
    assert res_a.hops[0].from_node.file_path == "src/file_a.py"
    assert res_a.hops[0].to_node.file_path == "src/file_a.py"


def test_mutual_recursion_cycle_detection_in_call_tree_and_call_stack(tmp_path: Path) -> None:
    """Verify mutual recursion (A -> B -> A) does not loop infinitely and flags cycles."""
    db_file = tmp_path / "cycle_test.db"
    db = IndexerDB(str(db_file))

    node_ping = Node(
        id="srv.py:ping:5",
        name="ping",
        qualified_name="ping",
        kind="function",
        file_path="src/srv.py",
        start_line=5,
        end_line=10,
    )
    node_pong = Node(
        id="srv.py:pong:15",
        name="pong",
        qualified_name="pong",
        kind="function",
        file_path="src/srv.py",
        start_line=15,
        end_line=20,
    )
    db.upsert_node(node_ping)
    db.upsert_node(node_pong)

    # Cycle: ping -> pong -> ping
    db.upsert_edge(
        Edge(
            from_node_id=node_ping.id,
            to_node_id=node_pong.id,
            relation_type="calls",
            call_line=8,
        )
    )
    db.upsert_edge(
        Edge(
            from_node_id=node_pong.id,
            to_node_id=node_ping.id,
            relation_type="calls",
            call_line=18,
        )
    )

    # 1. Call tree cycle detection
    tree_res = build_call_tree(db, "ping", max_depth=5)
    assert tree_res.found is True
    assert tree_res.tree is not None
    # ping -> pong -> ping(cycle=True)
    assert len(tree_res.tree.children) == 1
    child_pong = tree_res.tree.children[0]
    assert child_pong.name == "pong"
    assert len(child_pong.children) == 1
    grandchild_ping = child_pong.children[0]
    assert grandchild_ping.name == "ping"
    assert grandchild_ping.is_cycle is True
    # Verify traversal terminated cleanly at cycle boundary
    assert len(grandchild_ping.children) == 0

    # 2. Call stack cycle detection
    found, output, _nodes = build_call_stack(db, "ping", direction="down", max_depth=5)
    assert found is True
    assert any("CYCLE DETECTED" in line for line in output)


def test_blast_radius_impact_on_empty_and_leaf_files(tmp_path: Path) -> None:
    """Verify impact analysis handles unlinked files, empty files, and leaf nodes gracefully."""
    db_file = tmp_path / "impact_test.db"
    db = IndexerDB(str(db_file))

    # A standalone utility file with no incoming dependents
    leaf_node = Node(
        id="utils.py:format_time:1",
        name="format_time",
        qualified_name="format_time",
        kind="function",
        file_path="src/utils.py",
        start_line=1,
        end_line=5,
    )
    db.upsert_node(leaf_node)

    # 1. Leaf node file with zero incoming callers
    total_impact, impacts = compute_impact_analysis(db, "src/utils.py")
    assert total_impact == 0
    assert impacts == {1: [], 2: [], 3: []}

    # 2. Completely unindexed / non-existent file
    total_impact_none, impacts_none = compute_impact_analysis(db, "src/nonexistent.py")
    assert total_impact_none == 0
    assert impacts_none == {}


def test_caller_guard_disambiguation_centrality(tmp_path: Path) -> None:
    """Ensure duplicate symbol names are disambiguated by caller count/centrality."""
    db_file = tmp_path / "test.db"
    db = IndexerDB(db_path=str(db_file))

    # Candidate 1: active worker implementation in src/worker.py (3 callers)
    worker_file = tmp_path / "src" / "worker.py"
    worker_file.parent.mkdir(parents=True, exist_ok=True)
    worker_file.write_text("def process_item(item):\n    return item\n", encoding="utf-8")

    node_active = Node(
        id="worker:process_item",
        name="process_item",
        qualified_name="process_item",
        kind="function",
        file_path="src/worker.py",
        start_line=1,
        end_line=2,
    )
    db.upsert_node(node_active)

    # Candidate 2: legacy uncalled implementation in src/legacy.py (0 callers)
    legacy_file = tmp_path / "src" / "legacy.py"
    legacy_file.write_text("def process_item(item):\n    pass\n", encoding="utf-8")

    node_legacy = Node(
        id="legacy:process_item",
        name="process_item",
        qualified_name="process_item",
        kind="function",
        file_path="src/legacy.py",
        start_line=1,
        end_line=2,
    )
    db.upsert_node(node_legacy)

    # Add 3 callers to worker:process_item
    for idx in range(1, 4):
        caller_nid = f"caller_{idx}:run"
        caller_file = tmp_path / "src" / f"caller_{idx}.py"
        caller_file.write_text("def run(): process_item(1)\n", encoding="utf-8")
        db.upsert_node(
            Node(
                id=caller_nid,
                name="run",
                qualified_name=f"caller_{idx}.run",
                kind="function",
                file_path=f"src/caller_{idx}.py",
                start_line=1,
                end_line=1,
            )
        )
        db.upsert_edge(
            Edge(
                from_node_id=caller_nid,
                to_node_id="worker:process_item",
                relation_type="calls",
                call_line=1,
            )
        )

    # 1. Query without file_path: should select src/worker.py
    report = CallerGuardService.get_caller_guard(db, tmp_path, "process_item")
    assert report.found is True
    assert report.file_path == "src/worker.py"
    assert report.total_callers == 3
    assert len(report.alternative_candidates) == 1
    assert "src/legacy.py" in report.alternative_candidates[0]
    assert "Disambiguation: Selected 'src/worker.py'" in report.safety_advisory

    md = format_caller_guard_md(report)
    assert "Alternate Symbols Named `process_item`:" in md
    assert "src/legacy.py:L1 (0 callers)" in md

    # 2. Query with explicit file_path: should strictly select src/legacy.py
    report_explicit = CallerGuardService.get_caller_guard(
        db, tmp_path, "process_item", file_path="src/legacy.py"
    )
    assert report_explicit.found is True
    assert report_explicit.file_path == "src/legacy.py"
    assert report_explicit.total_callers == 0
    assert len(report_explicit.alternative_candidates) == 0


def test_vibe_bundle_polyglot_typescript(tmp_path: Path) -> None:
    """Verify TypeScript code slicing and test pattern discovery with tsx/ts fences."""
    db_file = tmp_path / "test_ts.db"
    db = IndexerDB(db_path=str(db_file))

    # TypeScript source
    ts_file = tmp_path / "src" / "authService.ts"
    ts_file.parent.mkdir(parents=True, exist_ok=True)
    ts_file.write_text(
        "export function verifyToken(token: string): boolean {\n"
        "    return token.startsWith('Bearer ');\n"
        "}\n",
        encoding="utf-8",
    )

    db.upsert_node(
        Node(
            id="auth:verifyToken",
            name="verifyToken",
            qualified_name="verifyToken",
            kind="function",
            file_path="src/authService.ts",
            start_line=1,
            end_line=3,
            signature="(token: string): boolean",
        )
    )

    # TypeScript sibling test
    test_file = tmp_path / "src" / "authService.test.ts"
    test_file.write_text(
        "import { verifyToken } from './authService';\n"
        "describe('verifyToken', () => {\n"
        "    it('should validate bearer token', () => {\n"
        "        expect(verifyToken('Bearer xyz')).toBe(true);\n"
        "    });\n"
        "});\n",
        encoding="utf-8",
    )

    bundle = VibeBundleService.assemble_bundle(
        db, tmp_path, intent="token verification", target_symbol="verifyToken", token_budget=1200
    )
    assert bundle.target_symbol == "verifyToken"
    assert bundle.test_context is not None
    assert bundle.test_context.found is True
    assert bundle.test_context.test_file == "src/authService.test.ts"

    prompt = bundle.formatted_prompt
    assert "```typescript" in prompt
    assert "verifyToken" in prompt
    assert "should validate bearer token" in prompt


def test_vibe_bundle_polyglot_go(tmp_path: Path) -> None:
    """Verify Go code slicing and sibling test pattern discovery."""
    db_file = tmp_path / "test_go.db"
    db = IndexerDB(db_path=str(db_file))

    go_file = tmp_path / "pkg" / "hash.go"
    go_file.parent.mkdir(parents=True, exist_ok=True)
    go_file.write_text(
        "package pkg\n\nfunc HashSum(input string) string {\n    return input\n}\n",
        encoding="utf-8",
    )

    db.upsert_node(
        Node(
            id="pkg:HashSum",
            name="HashSum",
            qualified_name="pkg.HashSum",
            kind="function",
            file_path="pkg/hash.go",
            start_line=3,
            end_line=5,
            signature="(input string) string",
        )
    )

    go_test = tmp_path / "pkg" / "hash_test.go"
    go_test.write_text(
        'package pkg\n\nimport "testing"\n\n'
        'func TestHashSum(t *testing.T) {\n    _ = HashSum("abc")\n}\n',
        encoding="utf-8",
    )

    bundle = VibeBundleService.assemble_bundle(
        db, tmp_path, intent="hash string sum", target_symbol="HashSum", token_budget=1000
    )
    assert bundle.test_context is not None
    assert bundle.test_context.found is True
    assert bundle.test_context.test_file == "pkg/hash_test.go"
    assert bundle.test_context.test_function == "TestHashSum"

    prompt = bundle.formatted_prompt
    assert "```go" in prompt


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
