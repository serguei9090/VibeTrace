"""C++ (fmtlib/fmt) end-to-end CLI benchmark stack."""

from __future__ import annotations

from tests.benchmarks.e2e_suite.common import StackSpec, StepResult, run_stack_suite

FMT_SPEC = StackSpec(
    stack="cpp",
    repo_dir_name="fmt",
    repo_url="https://github.com/fmtlib/fmt.git",
    summary_file="include/fmt/format.h",
    impact_file="include/fmt/format.h",
    deps_file="src/fmt.cc",
    search_name="to_string",
    search_sig="FMT_NODISCARD",
    semantic_query="to_string format_as digits10 integer formatting",
    semantic_expect="to_string",
    primary_symbol="to_string",
    callee_symbol="max_of",
    doc_catalog_path="doc",
    doc_search_query="fmt format syntax api",
    doc_search_expect="doc/",
)


def run_cpp_fmt() -> list[StepResult]:
    """Run all core VibeTrace CLI commands against fmtlib/fmt."""
    return run_stack_suite(FMT_SPEC)
