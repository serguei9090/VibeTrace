"""C# (AutoMapper/AutoMapper) end-to-end CLI benchmark stack."""

from __future__ import annotations

from tests.benchmarks.e2e_suite.common import StackSpec, StepResult, run_stack_suite

AUTOMAPPER_SPEC = StackSpec(
    stack="csharp",
    repo_dir_name="automapper",
    repo_url="https://github.com/AutoMapper/AutoMapper.git",
    summary_file="src/AutoMapper/Mapper.cs",
    impact_file="src/AutoMapper/Mapper.cs",
    deps_file="src/AutoMapper/Configuration/MapperConfiguration.cs",
    search_name="Mapper.Map",
    search_sig="IServiceCollection",
    semantic_query="Mapper Map ResolutionContext object mapping",
    semantic_expect="Mapper",
    primary_symbol="Mapper.Map",
    callee_symbol="ResolutionContext.Map",
    doc_catalog_path="docs",
    doc_search_query="CreateMap Profile MapperConfiguration",
    doc_search_expect="docs/",
)


def run_csharp_automapper() -> list[StepResult]:
    """Run all core VibeTrace CLI commands against AutoMapper/AutoMapper."""
    return run_stack_suite(AUTOMAPPER_SPEC)
