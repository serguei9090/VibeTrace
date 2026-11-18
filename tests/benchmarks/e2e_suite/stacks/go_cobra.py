"""Go (spf13/cobra) end-to-end CLI benchmark stack."""

from __future__ import annotations

from tests.benchmarks.e2e_suite.common import StackSpec, StepResult, run_stack_suite

COBRA_SPEC = StackSpec(
    stack="go",
    repo_dir_name="cobra",
    repo_url="https://github.com/spf13/cobra.git",
    summary_file="command.go",
    impact_file="command.go",
    deps_file="command.go",
    search_name="AddCommand",
    search_sig="cmds",
    semantic_query="AddCommand CommandPath child command",
    semantic_expect="AddCommand",
    primary_symbol="Command.AddCommand",
    callee_symbol="Command.CommandPath",
    doc_catalog_path="site/content",
    doc_search_query="Command Execute flags",
    doc_search_expect="site/content",
)


def run_go_cobra() -> list[StepResult]:
    """Run all core VibeTrace CLI commands against spf13/cobra."""
    return run_stack_suite(COBRA_SPEC)
