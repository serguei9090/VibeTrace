"""Ruby (rack/rack) end-to-end CLI benchmark stack."""

from __future__ import annotations

from tests.benchmarks.e2e_suite.common import StackSpec, StepResult, run_stack_suite

RACK_SPEC = StackSpec(
    stack="ruby",
    repo_dir_name="rack",
    repo_url="https://github.com/rack/rack.git",
    summary_file="lib/rack/lint.rb",
    impact_file="lib/rack/lint.rb",
    deps_file="lib/rack/multipart.rb",
    search_name="Rack.Lint.call",
    search_sig="env = nil",
    semantic_query="Rack Lint call middleware response validation",
    semantic_expect="Lint",
    primary_symbol="Rack.Lint.call",
    callee_symbol="Rack.Lint.Wrapper.response",
    doc_catalog_path="README.md",
    doc_search_query="Rack middleware call env response",
    doc_search_expect="README.md",
)


def run_ruby_rack() -> list[StepResult]:
    """Run all core VibeTrace CLI commands against rack/rack."""
    return run_stack_suite(RACK_SPEC)
