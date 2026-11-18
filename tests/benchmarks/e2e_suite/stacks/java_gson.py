"""Java (google/gson) end-to-end CLI benchmark stack."""

from __future__ import annotations

from tests.benchmarks.e2e_suite.common import StackSpec, StepResult, run_stack_suite

GSON_SPEC = StackSpec(
    stack="java",
    repo_dir_name="gson",
    repo_url="https://github.com/google/gson.git",
    summary_file="gson/src/main/java/com/google/gson/GsonBuilder.java",
    impact_file="gson/src/main/java/com/google/gson/GsonBuilder.java",
    deps_file="gson/src/main/java/com/google/gson/GsonBuilder.java",
    search_name="registerTypeAdapter",
    search_sig="TypeAdapter",
    semantic_query="registerTypeAdapter TypeAdapter GsonBuilder",
    semantic_expect="registerTypeAdapter",
    primary_symbol="GsonBuilder.registerTypeAdapter",
    callee_symbol="GsonBuilder.hasNonOverridableAdapter",
    doc_catalog_path="UserGuide.md",
    doc_search_query="JsonSerializer TypeAdapter",
    doc_search_expect="UserGuide.md",
)


def run_java_gson() -> list[StepResult]:
    """Run all core VibeTrace CLI commands against google/gson."""
    return run_stack_suite(GSON_SPEC)
