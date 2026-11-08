"""End-to-End integration test suite for multi-language CodeIngestTest fixture."""

from __future__ import annotations

from pathlib import Path

from vibetrace.indexer.db import IndexerDB
from vibetrace.indexer.main import reindex_all
from vibetrace.services.call_tree_service import build_call_tree
from vibetrace.services.graph_service import (
    compute_impact_analysis,
    get_code_slice,
    trace_execution_path,
)
from vibetrace.services.hybrid_search import hybrid_search


def test_code_ingest_multilanguage_e2e(tmp_path: Path) -> None:
    """Verify full VibeTrace capabilities across Python, TS, Go, Rust, C++, and Java."""
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


def test_cli_code_ingest_per_language_gates(tmp_path: Path) -> None:
    """Run CLI workflows on each isolated language project in CodeIngestTest with quality gates."""
    import io
    import json
    import shutil
    from contextlib import redirect_stdout

    from vibetrace.cli import build_parser

    root_dir = Path(__file__).resolve().parent.parent / "CodeIngestTest"
    assert root_dir.exists(), f"CodeIngestTest missing at {root_dir}"

    parser = build_parser()

    # Per-language configuration gates for happy path and negative path
    lang_specs = [
        {
            "lang": "python",
            "search_symbol": "ServiceClient",
            "search_negative": "NonExistentPySymbol99",
            "trace_from": "execute_job",
            "trace_to": "DataProcessor.format_token",
            "trace_negative": "NonExistentPyTarget99",
            "slice_symbol": "ServiceClient.run_pipeline",
            "slice_negative": "NonExistentPyMethod99",
            "expected_slice_snippet": "process_payload",
            "call_stack_symbol": "execute_job",
            "min_trace_hops": 2,
        },
        {
            "lang": "typescript",
            "search_symbol": "AuthService",
            "search_negative": "NonExistentTsSymbol99",
            "trace_from": "handleRegistration",
            "trace_to": "UserManager.createUser",
            "trace_negative": "NonExistentTsTarget99",
            "slice_symbol": "AuthService.register",
            "slice_negative": "NonExistentTsMethod99",
            "expected_slice_snippet": "createUser",
            "call_stack_symbol": "handleRegistration",
            "min_trace_hops": 2,
        },
        {
            "lang": "go",
            "search_symbol": "WebServer",
            "search_negative": "NonExistentGoSymbol99",
            "trace_from": "StartServer",
            "trace_to": "Database.Query",
            "trace_negative": "NonExistentGoTarget99",
            "slice_symbol": "WebServer.HandleRequest",
            "slice_negative": "NonExistentGoMethod99",
            "expected_slice_snippet": "s.Query",
            "call_stack_symbol": "StartServer",
            "min_trace_hops": 2,
        },
        {
            "lang": "rust",
            "search_symbol": "Scheduler",
            "search_negative": "NonExistentRsSymbol99",
            "trace_from": "run_scheduler",
            "trace_to": "Worker.execute_task",
            "trace_negative": "NonExistentRsTarget99",
            "slice_symbol": "Scheduler.schedule_task",
            "slice_negative": "NonExistentRsMethod99",
            "expected_slice_snippet": "execute_task",
            "call_stack_symbol": "run_scheduler",
            "min_trace_hops": 2,
        },
        {
            "lang": "cpp",
            "search_symbol": "Calculator",
            "search_negative": "NonExistentCppSymbol99",
            "trace_from": "calculate_total",
            "trace_to": "Calculator.add",
            "trace_negative": "NonExistentCppTarget99",
            "slice_symbol": "calculate_total",
            "slice_negative": "NonExistentCppMethod99",
            "expected_slice_snippet": "calc.add",
            "call_stack_symbol": "calculate_total",
            "min_trace_hops": 1,
        },
        {
            "lang": "java",
            "search_symbol": "CheckoutService",
            "search_negative": "NonExistentJavaSymbol99",
            "trace_from": "processOrder",
            "trace_to": "PaymentGateway.charge",
            "trace_negative": "NonExistentJavaTarget99",
            "slice_symbol": "CheckoutService.processOrder",
            "slice_negative": "NonExistentJavaMethod99",
            "expected_slice_snippet": "gateway.charge",
            "call_stack_symbol": "processOrder",
            "min_trace_hops": 1,
        },
    ]

    for spec in lang_specs:
        lang = spec["lang"]
        src_folder = root_dir / lang
        assert src_folder.exists(), f"Source folder {src_folder} not found"

        # Create dedicated isolated directory with local .vibetrace context
        dest_folder = tmp_path / lang
        shutil.copytree(src_folder, dest_folder)
        (dest_folder / ".vibetrace").mkdir(parents=True, exist_ok=True)

        # Gate 1: CLI Indexing
        buf_idx = io.StringIO()
        with redirect_stdout(buf_idx):
            args_idx = parser.parse_args(
                ["index", str(dest_folder), "--root", str(dest_folder), "--json"]
            )
            rc_idx = args_idx.func(args_idx)
        assert rc_idx == 0, f"Indexing failed for {lang}"
        idx_json = json.loads(buf_idx.getvalue())
        assert idx_json.get("status") == "ok", f"Invalid index status for {lang}"

        # Verify dedicated .vibetrace / vibetrace.db exists in project directory
        db_file = dest_folder / ".vibetrace" / "vibetrace.db"
        assert db_file.exists(), f"Local SQLite database not found at {db_file}"

        # Gate 2: CLI Search - Happy path
        buf_search = io.StringIO()
        with redirect_stdout(buf_search):
            args_search = parser.parse_args(
                [
                    "search",
                    spec["search_symbol"],
                    "--path",
                    str(dest_folder),
                    "--root",
                    str(dest_folder),
                    "--json",
                ]
            )
            rc_search = args_search.func(args_search)
        assert rc_search == 0
        search_res = json.loads(buf_search.getvalue())
        assert len(search_res) > 0, f"Search returned empty for {lang}: {spec['search_symbol']}"
        assert any(spec["search_symbol"].lower() in r["name"].lower() for r in search_res)

        # Gate 3: CLI Search - Negative path (non-existent symbol)
        buf_search_neg = io.StringIO()
        with redirect_stdout(buf_search_neg):
            args_search_neg = parser.parse_args(
                [
                    "search",
                    spec["search_negative"],
                    "--path",
                    str(dest_folder),
                    "--root",
                    str(dest_folder),
                    "--json",
                ]
            )
            rc_search_neg = args_search_neg.func(args_search_neg)
        assert rc_search_neg == 0
        search_neg_res = json.loads(buf_search_neg.getvalue())
        assert search_neg_res == [], f"Expected empty search for {spec['search_negative']}"

        # Gate 4: CLI Trace - Happy path
        buf_trace = io.StringIO()
        with redirect_stdout(buf_trace):
            args_trace = parser.parse_args(
                [
                    "trace",
                    spec["trace_from"],
                    spec["trace_to"],
                    "--path",
                    str(dest_folder),
                    "--root",
                    str(dest_folder),
                    "--json",
                ]
            )
            rc_trace = args_trace.func(args_trace)
        assert rc_trace == 0
        trace_res = json.loads(buf_trace.getvalue())
        assert trace_res.get("found") is True, (
            f"Trace failed for {lang}: {spec['trace_from']} -> {spec['trace_to']}"
        )
        assert len(trace_res.get("hops", [])) >= spec["min_trace_hops"]

        # Gate 5: CLI Trace - Negative path (non-existent target)
        buf_trace_neg = io.StringIO()
        with redirect_stdout(buf_trace_neg):
            args_trace_neg = parser.parse_args(
                [
                    "trace",
                    spec["trace_from"],
                    spec["trace_negative"],
                    "--path",
                    str(dest_folder),
                    "--root",
                    str(dest_folder),
                    "--json",
                ]
            )
            rc_trace_neg = args_trace_neg.func(args_trace_neg)
        assert rc_trace_neg == 1  # CLI returns 1 on symbol not found
        trace_neg_res = json.loads(buf_trace_neg.getvalue())
        assert trace_neg_res.get("found") is False

        # Gate 6: CLI Slice - Happy path
        buf_slice = io.StringIO()
        with redirect_stdout(buf_slice):
            args_slice = parser.parse_args(
                [
                    "slice",
                    spec["slice_symbol"],
                    "--path",
                    str(dest_folder),
                    "--root",
                    str(dest_folder),
                    "--json",
                ]
            )
            rc_slice = args_slice.func(args_slice)
        assert rc_slice == 0
        slice_res = json.loads(buf_slice.getvalue())
        assert slice_res.get("found") is True
        assert spec["expected_slice_snippet"] in slice_res.get("source_code", "")

        # Gate 7: CLI Slice - Negative path
        buf_slice_neg = io.StringIO()
        with redirect_stdout(buf_slice_neg):
            args_slice_neg = parser.parse_args(
                [
                    "slice",
                    spec["slice_negative"],
                    "--path",
                    str(dest_folder),
                    "--root",
                    str(dest_folder),
                    "--json",
                ]
            )
            rc_slice_neg = args_slice_neg.func(args_slice_neg)
        assert rc_slice_neg == 1
        slice_neg_res = json.loads(buf_slice_neg.getvalue())
        assert slice_neg_res.get("found") is False

        # Gate 8: CLI Call-Stack - Happy path
        buf_stack = io.StringIO()
        with redirect_stdout(buf_stack):
            args_stack = parser.parse_args(
                [
                    "call-stack",
                    spec["call_stack_symbol"],
                    "--path",
                    str(dest_folder),
                    "--root",
                    str(dest_folder),
                    "--json",
                ]
            )
            rc_stack = args_stack.func(args_stack)
        assert rc_stack == 0
        stack_res = json.loads(buf_stack.getvalue())
        assert stack_res.get("found") is True
        assert len(stack_res.get("call_stack", [])) > 0

        # Gate 9: CLI Call-Stack - Negative path
        buf_stack_neg = io.StringIO()
        with redirect_stdout(buf_stack_neg):
            args_stack_neg = parser.parse_args(
                [
                    "call-stack",
                    "NonExistentStackSymbol99",
                    "--path",
                    str(dest_folder),
                    "--root",
                    str(dest_folder),
                    "--json",
                ]
            )
            rc_stack_neg = args_stack_neg.func(args_stack_neg)
        assert rc_stack_neg == 1
        stack_neg_res = json.loads(buf_stack_neg.getvalue())
        assert stack_neg_res.get("found") is False
