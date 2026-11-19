"""PHP (Seldaek/monolog) end-to-end CLI benchmark stack."""

from __future__ import annotations

from tests.benchmarks.e2e_suite.common import StackSpec, StepResult, run_stack_suite

MONOLOG_SPEC = StackSpec(
    stack="php",
    repo_dir_name="monolog",
    repo_url="https://github.com/Seldaek/monolog.git",
    summary_file="src/Monolog/Logger.php",
    impact_file="src/Monolog/Logger.php",
    deps_file="src/Monolog/Handler/FingersCrossedHandler.php",
    search_name="FingersCrossedHandler",
    search_sig="LogRecord",
    semantic_query="FingersCrossedHandler handle activate LogRecord",
    semantic_expect="FingersCrossedHandler",
    primary_symbol="FingersCrossedHandler.handle",
    callee_symbol="FingersCrossedHandler.activate",
    doc_catalog_path="doc",
    doc_search_query="Logger pushHandler StreamHandler",
    doc_search_expect="doc/",
)


def run_php_monolog() -> list[StepResult]:
    """Run all core VibeTrace CLI commands against Seldaek/monolog."""
    return run_stack_suite(MONOLOG_SPEC)
