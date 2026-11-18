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
        setup_steps.append(("index . --force", ["index", ".", "--force", "--json"], 600))
    setup_steps.append(("index . (incremental)", ["index", ".", "--json"], 600))
    setup_steps.append(
        ("index . --embed (Rust vectors)", ["index", ".", "--embed", "--no-lsp", "--json"], 120)
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

    query_steps: list[tuple[str, list[str], int, str | None]] = [
        ("summary", ["summary", "fastapi/applications.py", "--json"], 60, None),
        ("search (name)", ["search", "FastAPI", "--json"], 60, None),
        ("search (--signature)", ["search", "add_api_route", "--signature", "--json"], 60, None),
        (
            "search (semantic vector)",
            ["search", "OAuth2 password bearer token authentication", "--json"],
            60,
            "OAuth2PasswordBearer",
        ),
        (
            "trace",
            ["trace", "FastAPI.add_api_route", "APIRouter.add_api_route", "--json"],
            60,
            None,
        ),
        ("call-tree", ["call-tree", "FastAPI.add_api_route", "--json"], 60, None),
        ("call-stack", ["call-stack", "FastAPI.add_api_route", "--json"], 60, None),
        ("impact", ["impact", "fastapi/applications.py", "--json"], 60, None),
        ("slice", ["slice", "FastAPI.add_api_route", "--json"], 60, None),
        ("references", ["references", "FastAPI.add_api_route", "--json"], 60, None),
        ("dependencies", ["dependencies", "fastapi/applications.py", "--json"], 60, None),
        ("dead-code (--no-lsp)", ["dead-code", "--no-lsp", "--json"], 60, None),
        (
            "dead-code (--exclude-exported)",
            ["dead-code", "--exclude-exported", "--json"],
            180,
            None,
        ),
        ("diagnose", ["diagnose", "FastAPI.add_api_route", "--json"], 60, None),
        ("audit splits", ["audit", "splits", "--json"], 60, None),
        (
            "audit splits (--max-lines 600 --margin 100)",
            ["audit", "splits", "--max-lines", "600", "--margin", "100", "--json"],
            60,
            None,
        ),
        (
            "audit security (normal)",
            ["audit", "security", "--skip-semgrep", "--skip-trivy", "--json"],
            120,
            None,
        ),
        (
            "audit security (semgrep)",
            ["audit", "security", "--skip-trivy", "--json"],
            120,
            None,
        ),
        ("audit quality (normal)", ["audit", "quality", "--file", "fastapi", "--json"], 120, None),
        (
            "audit quality (sonar)",
            ["audit", "quality", "--file", "fastapi", "--sonar", "--json"],
            120,
            None,
        ),
        ("guard", ["guard", "FastAPI.add_api_route", "--json"], 60, None),
        ("bundle", ["bundle", "FastAPI.add_api_route", "--json"], 60, None),
        ("docs catalog list", ["docs", "catalog", "list", "--json"], 60, None),
        (
            "docs search (hybrid vector)",
            ["docs", "search", "OAuth2 password bearer token security", "--json"],
            60,
            "oauth2",
        ),
        ("docs sync-links", ["docs", "sync-links", "--json"], 60, None),
    ]

    for name, args, timeout, expect_substr in query_steps:
        results.append(
            run_step("python", name, args, repo, timeout=timeout, expect_substr=expect_substr)
        )
    return results
