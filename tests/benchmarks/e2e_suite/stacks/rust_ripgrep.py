"""Rust (BurntSushi/ripgrep) end-to-end CLI benchmark stack."""

from __future__ import annotations

from tests.benchmarks.e2e_suite.common import StackSpec, StepResult, run_stack_suite

RIPGREP_SPEC = StackSpec(
    stack="rust",
    repo_dir_name="ripgrep",
    repo_url="https://github.com/BurntSushi/ripgrep.git",
    summary_file="crates/core/flags/parse.rs",
    impact_file="crates/core/flags/parse.rs",
    deps_file="crates/core/flags/parse.rs",
    search_name="parse_low_raw",
    search_sig="IntoIterator",
    semantic_query="parse_low_raw IntoIterator Parser",
    semantic_expect="parse_low_raw",
    primary_symbol="parse_low_raw",
    callee_symbol="Parser.new",
    doc_catalog_path="GUIDE.md",
    doc_search_query="regex pattern search",
    doc_search_expect="GUIDE.md",
)


def run_rust_ripgrep() -> list[StepResult]:
    """Run all core VibeTrace CLI commands against BurntSushi/ripgrep."""
    return run_stack_suite(RIPGREP_SPEC)
