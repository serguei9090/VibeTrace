"""Python (fastapi) end-to-end CLI benchmark stack."""

from __future__ import annotations

import json
import tomllib
from collections.abc import Callable
from pathlib import Path
from typing import Any

from tests.benchmarks.e2e_suite.common import REPOS_DIR, StepResult, ensure_repo, run_step

FASTAPI_URL = "https://github.com/fastapi/fastapi.git"


def _parse_json_list(stdout: str, label: str) -> tuple[list[dict[str, Any]] | None, str | None]:
    try:
        data = json.loads(stdout)
    except Exception as exc:
        return None, f"{label}: invalid JSON output ({exc})"
    if not isinstance(data, list) or not data:
        return None, f"{label}: expected non-empty JSON list"
    return [item for item in data if isinstance(item, dict)], None


def _validate_docs_catalog_and_config(repo: Path) -> Callable[[str], str | None]:
    def _validate(stdout: str) -> str | None:
        cats, err = _parse_json_list(stdout, "docs catalog list")
        if err or cats is None:
            return err
        by_name = {str(c.get("name")): c for c in cats}
        core = by_name.get("core")
        if not core:
            return "docs catalog list: missing 'core' catalog"
        core_paths = [str(p).replace("\\", "/").strip("/") for p in (core.get("paths") or [])]
        if "docs/en" not in core_paths:
            return f"docs catalog list: expected 'docs/en' in core paths, got {core_paths}"
        if "docs" in core_paths:
            return f"docs catalog list: unexpected bare 'docs' in core paths: {core_paths}"
        if "es_security" not in by_name:
            return "docs catalog list: missing 'es_security' catalog"

        cfg_path = repo / ".vibetrace.toml"
        if not cfg_path.is_file():
            return "missing .vibetrace.toml after init"
        try:
            cfg = tomllib.loads(cfg_path.read_text(encoding="utf-8"))
        except Exception as exc:
            return f"failed parsing .vibetrace.toml: {exc}"
        pref = cfg.get("docs", {}).get("preferred_locale")
        if pref != "en":
            return f"expected [docs].preferred_locale == 'en', got {pref!r}"
        return None

    return _validate


def _validate_docs_default(stdout: str) -> str | None:
    hits, err = _parse_json_list(stdout, "docs search")
    if err or hits is None:
        return err
    top_path = str(hits[0].get("file_path") or "").replace("\\", "/")
    if not top_path.startswith("docs/en/"):
        return f"docs search: expected #1 hit under docs/en/, got {top_path!r}"
    sources = {s for item in hits for s in (item.get("match_sources") or [])}
    missing = {"bm25", "vector"} - sources
    if missing:
        return f"docs search: missing match_sources {sorted(missing)} (saw {sorted(sources)})"
    return None


def _validate_docs_locale(expected_locale: str) -> Callable[[str], str | None]:
    prefix = f"docs/{expected_locale}/"

    def _validate(stdout: str) -> str | None:
        hits, err = _parse_json_list(stdout, f"docs search --locale {expected_locale}")
        if err or hits is None:
            return err
        bad_paths = [
            str(item.get("file_path") or "").replace("\\", "/")
            for item in hits
            if not str(item.get("file_path") or "").replace("\\", "/").startswith(prefix)
        ]
        if bad_paths:
            return (
                f"docs search --locale {expected_locale}: leaked non-locale paths {bad_paths[:3]}"
            )
        if not any("vector" in (item.get("match_sources") or []) for item in hits):
            return f"docs search --locale {expected_locale}: expected 'vector' in match_sources"
        return None

    return _validate


def _validate_semantic_oauth(stdout: str) -> str | None:
    hits, err = _parse_json_list(stdout, "search (semantic vector)")
    if err or hits is None:
        return err
    if not any("semantic" in (item.get("match_sources") or []) for item in hits[:5]):
        return "search (semantic vector): expected 'semantic' in top-5 match_sources"
    return None


def _validate_semantic_paraphrase(stdout: str) -> str | None:
    hits, err = _parse_json_list(stdout, "search (semantic paraphrase)")
    if err or hits is None:
        return err
    for item in hits[:5]:
        sources = item.get("match_sources") or []
        file_path = str(item.get("file_path") or "").replace("\\", "/")
        symbol_name = str(item.get("qualified_name") or item.get("name") or "").lower()
        if "semantic" in sources and ("fastapi/security" in file_path or "oauth" in symbol_name):
            return None
    return (
        "search (semantic paraphrase): no top-5 hit had 'semantic' with "
        "fastapi/security path or oauth symbol"
    )


def _validate_sonar_count(expect_positive: bool) -> Callable[[str], str | None]:
    def _validate(stdout: str) -> str | None:
        try:
            data = json.loads(stdout)
        except Exception as exc:
            return f"audit quality: invalid JSON output ({exc})"
        if not isinstance(data, dict):
            return "audit quality: expected JSON object"
        findings = data.get("findings") or []
        sonar_count = sum(
            1
            for f in findings
            if isinstance(f, dict) and (f.get("metadata") or {}).get("engine") == "sonarlint-ls"
        )
        if expect_positive and sonar_count <= 0:
            return f"audit quality (--file fastapi --sonar): expected sonar > 0, got {sonar_count}"
        if not expect_positive and sonar_count != 0:
            return (
                f"audit quality (bare --sonar): expected sonar == 0 on clean tree, "
                f"got {sonar_count}"
            )
        return None

    return _validate


def _validate_audit_scope(
    expected_mode: str,
    expect_zero_findings: bool = False,
    expect_positive_findings: bool = False,
) -> Callable[[str], str | None]:
    def _validate(stdout: str) -> str | None:
        try:
            data = json.loads(stdout)
        except Exception as exc:
            return f"audit JSON parse failed: {exc}"
        if not isinstance(data, dict):
            return "audit output is not a JSON object"
        mode = data.get("scope_mode")
        if mode != expected_mode:
            return f"expected scope_mode={expected_mode!r}, got {mode!r}"
        total = int(data.get("total_findings") or 0)
        if expect_zero_findings and total != 0:
            return f"expected 0 findings on clean tree, got {total}"
        if expect_positive_findings and total <= 0:
            return f"expected >0 findings for --all, got {total}"
        return None

    return _validate


def run_python_fastapi() -> list[StepResult]:
    """Run all core VibeTrace CLI commands against fastapi/fastapi."""
    repo = REPOS_DIR / "fastapi"
    ensure_repo(FASTAPI_URL, repo)
    db_exists = (repo / ".vibetrace" / "vibetrace.db").exists()

    setup_steps: list[tuple[str, list[str], int]] = [
        ("init (auto)", ["init", "--device", "gpu"], 900),
    ]
    if not db_exists:
        setup_steps.append(
            ("index . --force", ["index", ".", "--force", "--no-docs", "--json"], 600)
        )
    setup_steps.append(("index . (incremental)", ["index", ".", "--json"], 600))
    setup_steps.append(
        (
            "index . --embed (Rust vectors)",
            ["index", ".", "--embed", "--no-lsp", "--no-docs", "--json"],
            900,
        )
    )
    setup_steps.append(
        (
            "docs catalog add (es_security)",
            ["docs", "catalog", "add", "es_security", "docs/es/docs/tutorial/security", "--json"],
            60,
        )
    )
    setup_steps.append(
        (
            "docs index (es_security --force)",
            ["docs", "index", "es_security", "--force", "--json"],
            180,
        )
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

    query_steps: list[
        tuple[str, list[str], int, str | None, Callable[[str], str | None] | None]
    ] = [
        ("summary", ["summary", "fastapi/applications.py", "--json"], 60, None, None),
        ("search (name)", ["search", "FastAPI", "--json"], 60, None, None),
        (
            "search (--signature)",
            ["search", "add_api_route", "--signature", "--json"],
            60,
            None,
            None,
        ),
        (
            "search (semantic vector)",
            ["search", "OAuth2 password bearer token authentication", "--json"],
            60,
            "OAuth2PasswordBearer",
            _validate_semantic_oauth,
        ),
        (
            "search (semantic paraphrase)",
            ["search", "validate oauth credentials", "--json"],
            60,
            None,
            _validate_semantic_paraphrase,
        ),
        (
            "trace",
            ["trace", "FastAPI.add_api_route", "APIRouter.add_api_route", "--json"],
            60,
            None,
            None,
        ),
        ("call-tree", ["call-tree", "FastAPI.add_api_route", "--json"], 60, None, None),
        ("call-stack", ["call-stack", "FastAPI.add_api_route", "--json"], 60, None, None),
        ("impact", ["impact", "fastapi/applications.py", "--json"], 60, None, None),
        ("slice", ["slice", "FastAPI.add_api_route", "--json"], 60, None, None),
        ("references", ["references", "FastAPI.add_api_route", "--json"], 60, None, None),
        ("dependencies", ["dependencies", "fastapi/applications.py", "--json"], 60, None, None),
        ("dead-code (--no-lsp)", ["dead-code", "--no-lsp", "--json"], 60, None, None),
        (
            "dead-code (--exclude-exported)",
            ["dead-code", "--exclude-exported", "--json"],
            180,
            None,
            None,
        ),
        ("diagnose", ["diagnose", "FastAPI.add_api_route", "--json"], 60, None, None),
        ("audit splits", ["audit", "splits", "--json"], 60, None, None),
        (
            "audit splits (--max-lines 600 --margin 100)",
            ["audit", "splits", "--max-lines", "600", "--margin", "100", "--json"],
            60,
            None,
            None,
        ),
        (
            "audit security (git-diff default)",
            ["audit", "security", "--skip-semgrep", "--skip-trivy", "--json"],
            60,
            None,
            _validate_audit_scope("changed", expect_zero_findings=True),
        ),
        (
            "audit security (--all normal)",
            ["audit", "security", "--all", "--skip-semgrep", "--skip-trivy", "--json"],
            120,
            None,
            _validate_audit_scope("all"),
        ),
        (
            "audit security (--all semgrep)",
            ["audit", "security", "--all", "--skip-trivy", "--json"],
            120,
            None,
            _validate_audit_scope("all"),
        ),
        (
            "audit quality (git-diff default)",
            ["audit", "quality", "--json"],
            60,
            None,
            _validate_audit_scope("changed", expect_zero_findings=True),
        ),
        (
            "audit quality (--all whole repo)",
            ["audit", "quality", "--all", "--json"],
            120,
            None,
            _validate_audit_scope("all", expect_positive_findings=True),
        ),
        (
            "audit quality (sonar git-diff default)",
            ["audit", "quality", "--sonar", "--json"],
            60,
            None,
            _validate_sonar_count(expect_positive=False),
        ),
        (
            "audit quality (sonar --file fastapi)",
            ["audit", "quality", "--file", "fastapi", "--sonar", "--json"],
            240,
            None,
            _validate_sonar_count(expect_positive=True),
        ),
        ("guard", ["guard", "FastAPI.add_api_route", "--json"], 60, None, None),
        ("bundle", ["bundle", "FastAPI.add_api_route", "--json"], 60, None, None),
        (
            "docs catalog list",
            ["docs", "catalog", "list", "--json"],
            60,
            None,
            _validate_docs_catalog_and_config(repo),
        ),
        (
            "docs search (hybrid vector)",
            ["docs", "search", "OAuth2 password bearer token security", "--json"],
            60,
            "oauth2",
            _validate_docs_default,
        ),
        (
            "docs search (--locale en)",
            ["docs", "search", "OAuth2 password bearer token security", "--locale", "en", "--json"],
            60,
            None,
            _validate_docs_locale("en"),
        ),
        (
            "docs search (--locale es)",
            ["docs", "search", "OAuth2 password bearer token security", "--locale", "es", "--json"],
            60,
            None,
            _validate_docs_locale("es"),
        ),
        ("docs sync-links", ["docs", "sync-links", "--json"], 60, None, None),
    ]

    for name, args, timeout, expect_substr, validator in query_steps:
        results.append(
            run_step(
                "python",
                name,
                args,
                repo,
                timeout=timeout,
                expect_substr=expect_substr,
                validator=validator,
            )
        )
    return results
