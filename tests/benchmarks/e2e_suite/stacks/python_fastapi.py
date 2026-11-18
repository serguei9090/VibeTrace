"""Python (fastapi) end-to-end CLI benchmark stack."""

from __future__ import annotations

from tests.benchmarks.e2e_suite.common import REPOS_DIR, StepResult, ensure_repo, run_step

FASTAPI_URL = "https://github.com/fastapi/fastapi.git"


def run_python_fastapi() -> list[StepResult]:
    """Run all core VibeTrace CLI commands against fastapi/fastapi."""
    repo = REPOS_DIR / "fastapi"
    ensure_repo(FASTAPI_URL, repo)
    db_exists = (repo / ".vibetrace" / "vibetrace.db").exists()

    setup_steps: list[tuple[str, list[str], int]] = [
        ("init (auto)", ["init"], 600),
    ]
    if not db_exists:
        setup_steps.append(("index . --force", ["index", ".", "--force"], 600))
    setup_steps.append(("index . (incremental)", ["index", "."], 600))
    setup_steps.append(
        ("index . --embed (Rust vectors)", ["index", ".", "--embed", "--no-lsp"], 120)
    )

    results: list[StepResult] = []
    for name, args, timeout in setup_steps:
        res = run_step("python", name, args, repo, timeout=timeout)
        results.append(res)
        if not res.passed:
            print(
                f"[FATAL] Setup step '{name}' failed (exit={res.exit_code}): {res.detail}",
                flush=True,
            )
            return results

    query_steps: list[tuple[str, list[str], int]] = [
        ("summary", ["summary", "fastapi/applications.py", "--json"], 60),
        ("search (name)", ["search", "FastAPI", "--json"], 60),
        ("search (--signature)", ["search", "add_api_route", "--signature", "--json"], 60),
        ("trace", ["trace", "FastAPI.add_api_route", "APIRouter.add_api_route", "--json"], 60),
        ("call-tree", ["call-tree", "FastAPI.add_api_route", "--json"], 60),
        ("call-stack", ["call-stack", "FastAPI.add_api_route", "--json"], 60),
        ("impact", ["impact", "fastapi/applications.py", "--json"], 60),
        ("slice", ["slice", "FastAPI.add_api_route", "--json"], 60),
        ("references", ["references", "FastAPI.add_api_route", "--json"], 60),
        ("dependencies", ["dependencies", "fastapi/applications.py", "--json"], 60),
        ("dead-code (--no-lsp)", ["dead-code", "--no-lsp", "--json"], 60),
        ("dead-code (--exclude-exported)", ["dead-code", "--exclude-exported", "--json"], 180),
        ("diagnose", ["diagnose", "FastAPI.add_api_route", "--json"], 60),
        ("audit splits", ["audit", "splits", "--json"], 60),
        (
            "audit splits (--max-lines 400 --margin 100)",
            ["audit", "splits", "--max-lines", "400", "--margin", "100", "--json"],
            60,
        ),
        ("guard", ["guard", "FastAPI.add_api_route", "--json"], 60),
        ("bundle", ["bundle", "FastAPI.add_api_route", "--json"], 60),
        ("docs catalog list", ["docs", "catalog", "list", "--json"], 60),
        ("docs sync-links", ["docs", "sync-links", "--json"], 60),
    ]

    for name, args, timeout in query_steps:
        results.append(run_step("python", name, args, repo, timeout=timeout))
    return results
