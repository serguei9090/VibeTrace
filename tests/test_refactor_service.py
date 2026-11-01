"""Tests for AST Refactor Service and Split-Method Extraction."""

import argparse
import ast
from pathlib import Path

from vibetrace.cli_refactor import cmd_refactor
from vibetrace.services.refactor_service import RefactorService

SAMPLE_COMPLEX_FUNCTION = """
def process_data(items, multiplier):
    header = "START"
    accumulator = 0
    intermediate = []

    # Target block for extraction
    for item in items:
        if item > 0:
            val = item * multiplier
            intermediate.append(val)
            accumulator += val

    summary = f"Total: {accumulator}, Count: {len(intermediate)}"
    return summary
"""


def test_refactor_variable_lifetime_inputs_and_outputs(tmp_path: Path):
    f = tmp_path / "sample.py"
    f.write_text(SAMPLE_COMPLEX_FUNCTION, encoding="utf-8")

    candidates = RefactorService.suggest_split_candidates(
        project_root=tmp_path,
        file_path="sample.py",
        func_name="process_data",
    )

    assert len(candidates) >= 1
    top = candidates[0]
    assert top.complexity_reduction >= 2
    # items, multiplier are read inside loop from params
    assert "items" in top.inputs
    assert "multiplier" in top.inputs
    # accumulator is in inputs (read via +=) and outputs (used in summary)
    assert "accumulator" in top.inputs
    assert "accumulator" in top.outputs


def test_refactor_plan_and_diff_generation(tmp_path: Path):
    f = tmp_path / "sample.py"
    f.write_text(SAMPLE_COMPLEX_FUNCTION, encoding="utf-8")

    plan = RefactorService.build_refactor_plan(
        project_root=tmp_path,
        file_path="sample.py",
        func_name="process_data",
        start_line=8,
        end_line=12,
        helper_name="_calculate_totals",
    )

    assert plan.can_auto_apply is True
    assert plan.helper_name == "_calculate_totals"
    assert "def _calculate_totals(" in plan.helper_code
    assert "return accumulator" in plan.helper_code
    assert "accumulator = _calculate_totals(" in plan.call_replacement
    assert "--- a/sample.py" in plan.diff
    assert "+++ b/sample.py" in plan.diff

    # Verify that refactored source parses as valid Python AST
    parsed = ast.parse(plan.refactored_full_source)
    assert parsed is not None


def test_refactor_apply_and_execution(tmp_path: Path):
    f = tmp_path / "sample.py"
    f.write_text(SAMPLE_COMPLEX_FUNCTION, encoding="utf-8")

    plan = RefactorService.build_refactor_plan(
        project_root=tmp_path,
        file_path="sample.py",
        func_name="process_data",
        start_line=8,
        end_line=12,
        helper_name="_calculate_totals",
    )

    RefactorService.apply_refactor_plan(tmp_path, plan)

    # Re-read file and verify
    modified_code = f.read_text(encoding="utf-8")
    assert "def _calculate_totals(" in modified_code

    # Execute the modified code dynamically to verify runtime correctness
    namespace = {}
    exec(modified_code, namespace)
    func = namespace["process_data"]
    result = func([1, 2, 3], 2)
    # 1*2 + 2*2 + 3*2 = 12
    assert result == "Total: 12, Count: 3"


def test_refactor_rejection_rules(tmp_path: Path):
    yield_func = """
def generator_func(items):
    x = 1
    for item in items:
        yield item * x
    return x
"""
    f = tmp_path / "gen.py"
    f.write_text(yield_func, encoding="utf-8")

    plan = RefactorService.build_refactor_plan(
        project_root=tmp_path,
        file_path="gen.py",
        func_name="generator_func",
        start_line=4,
        end_line=5,
    )
    assert plan.can_auto_apply is False
    assert plan.rejection_reason is not None
    assert "yield" in plan.rejection_reason.lower()


def test_refactor_cli_integration(tmp_path: Path):
    f = tmp_path / "sample.py"
    f.write_text(SAMPLE_COMPLEX_FUNCTION, encoding="utf-8")

    # CLI Suggest
    args_suggest = argparse.Namespace(
        path=str(tmp_path),
        file="sample.py",
        function="process_data",
        suggest=True,
        json=True,
    )
    code = cmd_refactor(args_suggest)
    assert code == 0

    # CLI Dry-run
    args_dry = argparse.Namespace(
        path=str(tmp_path),
        file="sample.py",
        function="process_data",
        suggest=False,
        start_line=8,
        end_line=12,
        helper_name="_helper_sum",
        apply=False,
        json=False,
    )
    code = cmd_refactor(args_dry)
    assert code == 0

    # CLI Apply
    args_apply = argparse.Namespace(
        path=str(tmp_path),
        file="sample.py",
        function="process_data",
        suggest=False,
        start_line=8,
        end_line=12,
        helper_name="_helper_sum",
        apply=True,
        json=False,
    )
    code = cmd_refactor(args_apply)
    assert code == 0

    # Verify applied file
    mod_text = f.read_text(encoding="utf-8")
    assert "_helper_sum" in mod_text
