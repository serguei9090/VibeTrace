"""Tests for Predictive Test Selection Service and CLI."""

from pathlib import Path

from vibetrace.indexer.db import IndexerDB
from vibetrace.services.predictive_test_service import (
    ImpactedTest,
    PredictiveTestReport,
    PredictiveTestService,
)


def test_predictive_test_models():
    item = ImpactedTest(
        test_file="tests/test_demo.py",
        test_function="test_feature_a",
        target_symbol="calc_sum",
        target_file="src/math.py",
        hops=1,
        runner="pytest",
    )
    assert item.hops == 1
    assert item.runner == "pytest"

    report = PredictiveTestReport(
        target_symbols=["calc_sum"],
        changed_files=["src/math.py"],
        impacted_tests=[item],
        total_impacted=1,
        runner="pytest",
        command="pytest tests/test_demo.py -k test_feature_a",
    )
    assert report.total_impacted == 1
    assert report.command is not None


def test_resolve_impacted_tests_for_symbol(tmp_path: Path):
    db_file = tmp_path / "test_index.db"
    db = IndexerDB(str(db_file))

    with db._get_conn() as conn:
        # Create test nodes
        conn.execute(
            "INSERT INTO nodes (id, name, qualified_name, kind, file_path, start_line, end_line) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (
                "node_target",
                "compute_total",
                "billing.compute_total",
                "function",
                "src/billing.py",
                10,
                25,
            ),
        )
        conn.execute(
            "INSERT INTO nodes (id, name, qualified_name, kind, file_path, start_line, end_line) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (
                "node_caller",
                "process_invoice",
                "invoice.process_invoice",
                "function",
                "src/invoice.py",
                30,
                50,
            ),
        )
        conn.execute(
            "INSERT INTO nodes (id, name, qualified_name, kind, file_path, start_line, end_line) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (
                "node_test",
                "test_invoice_billing",
                "test_billing.test_invoice_billing",
                "function",
                "tests/test_billing.py",
                5,
                20,
            ),
        )

        # Edges: test -> caller -> target
        conn.execute(
            "INSERT INTO edges (from_node_id, to_node_id, relation_type, call_line) "
            "VALUES (?, ?, ?, ?)",
            ("node_test", "node_caller", "calls", 10),
        )
        conn.execute(
            "INSERT INTO edges (from_node_id, to_node_id, relation_type, call_line) "
            "VALUES (?, ?, ?, ?)",
            ("node_caller", "node_target", "calls", 35),
        )

    report = PredictiveTestService.resolve_impacted_tests(
        db=db,
        project_root=tmp_path,
        target_symbols=["compute_total"],
        max_hops=5,
    )

    assert report.total_impacted == 1
    assert report.target_symbols == ["compute_total"]
    hit = report.impacted_tests[0]
    assert hit.test_function == "test_invoice_billing"
    assert hit.test_file == "tests/test_billing.py"
    assert hit.hops == 2


def test_predictive_test_runner_args_construction():
    tests = [
        ImpactedTest(
            test_file="tests/test_a.py",
            test_function="test_one",
            target_symbol="sub",
            target_file="src/a.py",
            runner="pytest",
        ),
        ImpactedTest(
            test_file="tests/test_b.py",
            test_function="test_two",
            target_symbol="sub",
            target_file="src/b.py",
            runner="pytest",
        ),
    ]
    args = PredictiveTestService._build_runner_args(tests)
    assert any("pytest" in arg for arg in args)
    assert "tests/test_a.py" in args
    assert "tests/test_b.py" in args
    assert "-k" in args
    assert "test_one or test_two" in args


def test_predictive_test_dry_run_output(capsys):
    report = PredictiveTestReport(
        target_symbols=["sub"],
        changed_files=["src/a.py"],
        impacted_tests=[
            ImpactedTest(
                test_file="tests/test_a.py",
                test_function="test_sub_logic",
                target_symbol="sub",
                target_file="src/a.py",
                hops=1,
                runner="pytest",
            )
        ],
        total_impacted=1,
        runner="pytest",
        command="pytest tests/test_a.py -k test_sub_logic",
    )
    code = PredictiveTestService.execute_impacted_tests(report, dry_run=True)
    assert code == 0
    captured = capsys.readouterr()
    assert "[PREDICTIVE TEST] Impacted tests (1):" in captured.out
    assert "tests/test_a.py::test_sub_logic" in captured.out
