"""C (redis/hiredis) end-to-end CLI benchmark stack."""

from __future__ import annotations

from tests.benchmarks.e2e_suite.common import StackSpec, StepResult, run_stack_suite

HIREDIS_SPEC = StackSpec(
    stack="c",
    repo_dir_name="hiredis",
    repo_url="https://github.com/redis/hiredis.git",
    summary_file="hiredis.c",
    impact_file="hiredis.c",
    deps_file="async.c",
    search_name="redisFree",
    search_sig="redisContext",
    semantic_query="redisFree freeReplyObject redisContext disconnect",
    semantic_expect="redisFree",
    primary_symbol="redisFree",
    callee_symbol="hi_free",
    doc_catalog_path="README.md",
    doc_search_query="redisConnect redisCommand freeReplyObject",
    doc_search_expect="README.md",
)


def run_c_hiredis() -> list[StepResult]:
    """Run all core VibeTrace CLI commands against redis/hiredis."""
    return run_stack_suite(HIREDIS_SPEC)
