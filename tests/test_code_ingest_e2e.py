"""End-to-End integration test suite for multi-language CodeIngestTest fixture."""

from __future__ import annotations

from pathlib import Path

from vibegraph.indexer.db import IndexerDB
from vibegraph.indexer.main import reindex_all
from vibegraph.services.call_tree_service import build_call_tree
from vibegraph.services.graph_service import (
    compute_impact_analysis,
    get_code_slice,
    trace_execution_path,
)
from vibegraph.services.hybrid_search import hybrid_search


def test_code_ingest_multilanguage_e2e(tmp_path: Path) -> None:
    """Verify full VibeGraph capabilities across Python, TS, Go, Rust, C++, and Java."""
    root_dir = Path(__file__).resolve().parent.parent / "CodeIngestTest"
    assert root_dir.exists(), f"CodeIngestTest directory missing at {root_dir}"

    db_path = str(tmp_path / "test_ingest.db")
    db = IndexerDB(db_path)

    # 1. Index all languages
    reindex_all(db, str(root_dir), verbose=False, project_root=root_dir)

    all_files = db.get_all_indexed_file_paths()
    assert len(all_files) >= 12, f"Expected >= 12 indexed files, got {len(all_files)}"

    # 2. Verify Node counts and kinds across languages
    with db._get_conn() as conn:
        counts = conn.execute("SELECT kind, COUNT(*) as c FROM nodes GROUP BY kind").fetchall()
        kind_map = {row["kind"]: row["c"] for row in counts}

    assert kind_map.get("function", 0) >= 10
    assert kind_map.get("class", 0) + kind_map.get("struct", 0) >= 5
    assert kind_map.get("module", 0) >= 6

    # 3. Verify Edge relations
    with db._get_conn() as conn:
        edge_counts = conn.execute(
            "SELECT relation_type, COUNT(*) as c FROM edges GROUP BY relation_type"
        ).fetchall()
        edge_map = {row["relation_type"]: row["c"] for row in edge_counts}

    assert edge_map.get("defines", 0) >= 15
    assert edge_map.get("calls", 0) >= 4

    # 4. Hybrid Search across languages
    res_py = hybrid_search(db, "ServiceClient", limit=5)
    assert any("ServiceClient" in r.name for r in res_py)

    res_ts = hybrid_search(db, "AuthService", limit=5)
    assert any("AuthService" in r.name for r in res_ts)

    res_go = hybrid_search(db, "WebServer", limit=5)
    assert any("WebServer" in r.name for r in res_go)

    res_rs = hybrid_search(db, "Scheduler", limit=5)
    assert any("Scheduler" in r.name for r in res_rs)

    res_cpp = hybrid_search(db, "Calculator", limit=5)
    assert any("Calculator" in r.name for r in res_cpp)

    # 5. Surgical Code Slice across languages
    slice_py = get_code_slice(db, root_dir, "ServiceClient.run_pipeline")
    assert slice_py.found is True
    assert "process_payload" in slice_py.source_code

    slice_ts = get_code_slice(db, root_dir, "AuthService.register")
    assert slice_ts.found is True
    assert "createUser" in slice_ts.source_code

    slice_go = get_code_slice(db, root_dir, "WebServer.HandleRequest")
    assert slice_go.found is True
    assert "s.Query" in slice_go.source_code

    # 6. Call Tree exploration
    tree_py = build_call_tree(db, "execute_job", max_depth=4)
    assert tree_py.found is True
    assert tree_py.total_nodes >= 1

    tree_ts = build_call_tree(db, "handleRegistration", max_depth=4)
    assert tree_ts.found is True
    assert tree_ts.total_nodes >= 1

    # 7. Execution Tracepath across interconnected files
    trace_py = trace_execution_path(db, "execute_job", "DataProcessor.format_token")
    assert trace_py.found is True
    assert len(trace_py.hops) >= 2

    trace_ts = trace_execution_path(db, "handleRegistration", "UserManager.createUser")
    assert trace_ts.found is True
    assert len(trace_ts.hops) >= 2

    trace_go = trace_execution_path(db, "StartServer", "Database.Query")
    assert trace_go.found is True
    assert len(trace_go.hops) >= 2

    trace_rs = trace_execution_path(db, "run_scheduler", "Worker.execute_task")
    assert trace_rs.found is True
    assert len(trace_rs.hops) >= 2

    trace_cpp = trace_execution_path(db, "calculate_total", "Calculator.add")
    assert trace_cpp.found is True
    assert len(trace_cpp.hops) >= 1

    trace_java = trace_execution_path(db, "processOrder", "PaymentGateway.charge")
    assert trace_java.found is True
    assert len(trace_java.hops) >= 1

    # 8. Impact Analysis
    total_impact, _ = compute_impact_analysis(db, "python/service.py")
    assert total_impact >= 0

    # 9. Guard verification: non-positive limits safely return empty list
    assert hybrid_search(db, "ServiceClient", limit=0) == []
    assert hybrid_search(db, "ServiceClient", limit=-5) == []
