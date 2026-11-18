"""TypeScript (colinhacks/zod) end-to-end CLI benchmark stack."""

from __future__ import annotations

from tests.benchmarks.e2e_suite.common import StackSpec, StepResult, run_stack_suite

ZOD_SPEC = StackSpec(
    stack="typescript",
    repo_dir_name="zod",
    repo_url="https://github.com/colinhacks/zod.git",
    summary_file="packages/zod/src/v4/classic/from-json-schema.ts",
    impact_file="packages/zod/src/v4/core/core.ts",
    deps_file="packages/zod/src/v4/classic/from-json-schema.ts",
    search_name="convertBaseSchema",
    search_sig="JSONSchema",
    semantic_query="convertBaseSchema JSONSchema",
    semantic_expect="convertBaseSchema",
    primary_symbol="convertBaseSchema",
    callee_symbol="resolveRef",
    doc_catalog_path="README.md",
    doc_search_query="schema validation parse",
    doc_search_expect="README.md",
)


def run_typescript_zod() -> list[StepResult]:
    """Run all core VibeTrace CLI commands against colinhacks/zod."""
    return run_stack_suite(ZOD_SPEC)
