"""Unit tests verifying fixes for Cesar's external code review claims & proposals."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from unittest.mock import MagicMock, patch

from vibetrace.indexer.db import IndexerDB
from vibetrace.indexer.main import reindex_all
from vibetrace.security.adapters.semgrep import SemgrepAdapter
from vibetrace.security.ast_scanner import AstSecurityScanner
from vibetrace.security.rule_sync import RuleCatalogManager
from vibetrace.security.taint_engine import TaintEngine
from vibetrace.services.coverage_service import CoverageGateReport
from vibetrace.services.review_service import UnifiedReviewReport, format_unified_review_md
from vibetrace.services.trace_service import trace_execution_path


def test_claim1_empty_coverage_returns_none_and_na() -> None:
    """Claim 1: 0/0 target symbols must return None ('N/A') instead of 100.0%."""
    report = CoverageGateReport(total_target_symbols=0, tested_symbols_count=0)
    assert report.ast_symbol_coverage_pct is None
    assert report.effective_coverage_pct is None
    assert report.status == "N/A"

    review_report = UnifiedReviewReport(
        passed=True,
        coverage_gate=report,
    )
    md = format_unified_review_md(review_report)
    assert "N/A (0/0 symbols in scope" in md
    assert "100.0%" not in md


def test_claim2_elementary_path_traversal_open_detected(tmp_path: Path) -> None:
    """Claim 2: Elementary open(filename) from route param or request input triggers CWE-22."""
    vuln_file = tmp_path / "app.py"
    vuln_file.write_text(
        "from flask import Flask, request\n"
        "app = Flask(__name__)\n\n"
        "@app.route('/read')\n"
        "def read_file(user_path: str):\n"
        "    filename = request.args.get('file')\n"
        "    with open(filename, 'r') as f:\n"
        "        return f.read()\n",
        encoding="utf-8",
    )

    findings = AstSecurityScanner.scan_file("app.py", tmp_path)
    cwe22 = [f for f in findings if f.cwe_id == "CWE-22"]
    assert len(cwe22) >= 1

    # Also verify TaintEngine links the taint flow and escalates severity
    db = IndexerDB(tmp_path / ".vibetrace" / "index.db")
    reindex_all(db, str(tmp_path), verbose=False, project_root=tmp_path)
    enriched = TaintEngine.analyze(db, cwe22, project_root=tmp_path)
    assert any("Reachable from untrusted entry point" in f.message for f in enriched)


def test_claim2_sanitized_open_not_flagged(tmp_path: Path) -> None:
    """Sanitized open(os.path.basename(filename)) should not trigger false positive CWE-22."""
    safe_file = tmp_path / "safe_app.py"
    safe_file.write_text(
        "import os\n"
        "from flask import request\n\n"
        "def read_safe():\n"
        "    raw_name = request.args.get('file')\n"
        "    safe_name = os.path.basename(raw_name)\n"
        "    with open(safe_name, 'r') as f:\n"
        "        return f.read()\n",
        encoding="utf-8",
    )
    findings = AstSecurityScanner.scan_file("safe_app.py", tmp_path)
    cwe22 = [f for f in findings if f.cwe_id == "CWE-22"]
    assert len(cwe22) == 0


def test_claim3_proposal3_transitive_reexport_and_heuristic_fallback(tmp_path: Path) -> None:
    """Claim 3 & Proposal 3: Cross-file __init__.py re-exports and 2-level fallback."""
    pkg = tmp_path / "mypkg"
    pkg.mkdir()
    (pkg / "__init__.py").write_text(
        "from mypkg.service import run_pipeline\n",
        encoding="utf-8",
    )
    (pkg / "service.py").write_text(
        "def run_pipeline(x: int) -> int:\n"
        "    return helper_step(x)\n\n"
        "def helper_step(v: int) -> int:\n"
        "    return v + 1\n",
        encoding="utf-8",
    )
    (tmp_path / "cli_entry.py").write_text(
        "from mypkg import run_pipeline\n\ndef main_cmd() -> int:\n    return run_pipeline(42)\n",
        encoding="utf-8",
    )

    db_path = tmp_path / ".vibetrace" / "index.db"
    db = IndexerDB(db_path)
    reindex_all(db, str(tmp_path), verbose=False, project_root=tmp_path)

    trace_res = trace_execution_path(db, "main_cmd", "helper_step")
    assert trace_res.found is True
    path_names = [trace_res.hops[0].from_node.name] + [h.to_node.name for h in trace_res.hops]
    assert path_names == ["main_cmd", "run_pipeline", "helper_step"]


def test_claim3_proposal3_inferred_confidence_for_dynamic_dispatch(tmp_path: Path) -> None:
    """Proposal 3: Unresolved method call falls back to unique symbol with confidence='inferred'."""
    (tmp_path / "worker.py").write_text(
        "class TaskWorker:\n    def execute_unique_job(self) -> str:\n        return 'done'\n",
        encoding="utf-8",
    )
    (tmp_path / "dispatcher.py").write_text(
        "def dispatch_dynamic(worker_instance) -> str:\n"
        "    return worker_instance.execute_unique_job()\n",
        encoding="utf-8",
    )

    db_path = tmp_path / ".vibetrace" / "index.db"
    db = IndexerDB(db_path)
    reindex_all(db, str(tmp_path), verbose=False, project_root=tmp_path)

    trace_res = trace_execution_path(db, "dispatch_dynamic", "execute_unique_job")
    assert trace_res.found is True

    with db._get_conn() as conn:
        row = conn.execute("SELECT confidence FROM edges WHERE confidence = 'inferred'").fetchone()
        assert row is not None
        assert row["confidence"] == "inferred"


def test_claim7_proposal2_sha256_supply_chain_verification(tmp_path: Path) -> None:
    """Claim 7 & Proposal 2: Remote feed payload with mismatched SHA-256 is rejected."""
    payload = json.dumps({"rules": [{"id": "TEST-SHA-1", "pattern": "eval(...)"}]}).encode("utf-8")
    actual_sha = hashlib.sha256(payload).hexdigest()

    mock_resp = MagicMock()
    mock_resp.status = 200
    mock_resp.read.return_value = payload
    mock_resp.__enter__.return_value = mock_resp

    db = IndexerDB(tmp_path / ".vibetrace" / "index.db")
    RuleCatalogManager._ensure_table(db)

    with patch("urllib.request.urlopen", return_value=mock_resp):
        # Mismatched SHA-256 is rejected and nothing is cached
        RuleCatalogManager._refresh_from_feed(
            db, "https://example.com/rules.json", 1000.0, expected_sha256="0" * 64
        )
        with db._get_conn() as conn:
            cnt = conn.execute(
                "SELECT COUNT(*) FROM security_rules_cache WHERE rule_id = 'TEST-SHA-1'"
            ).fetchone()[0]
            assert cnt == 0

        # Matching SHA-256 succeeds and caches rule
        RuleCatalogManager._refresh_from_feed(
            db, "https://example.com/rules.json", 1000.0, expected_sha256=actual_sha
        )
        with db._get_conn() as conn:
            cnt = conn.execute(
                "SELECT COUNT(*) FROM security_rules_cache WHERE rule_id = 'TEST-SHA-1'"
            ).fetchone()[0]
            assert cnt == 1


def test_proposal1_semgrep_adapter_sarif_bridge(tmp_path: Path) -> None:
    """Proposal 1: SemgrepAdapter executes semgrep scan --sarif and ingests SARIF."""
    sample_sarif = {
        "version": "2.1.0",
        "runs": [
            {
                "tool": {"driver": {"name": "semgrep", "rules": []}},
                "results": [
                    {
                        "ruleId": "python.lang.security.audit.dangerous-eval",
                        "level": "error",
                        "message": {"text": "Detected dangerous eval()"},
                        "locations": [
                            {
                                "physicalLocation": {
                                    "artifactLocation": {"uri": "app.py"},
                                    "region": {"startLine": 10, "endLine": 10},
                                }
                            }
                        ],
                    }
                ],
            }
        ],
    }

    mock_proc = MagicMock()
    mock_proc.stdout = json.dumps(sample_sarif)
    mock_proc.returncode = 0

    with (
        patch.object(SemgrepAdapter, "is_available", return_value=True),
        patch("subprocess.run", return_value=mock_proc) as run_mock,
    ):
        findings = SemgrepAdapter.scan_project(tmp_path)
        assert run_mock.called
        cmd_args = run_mock.call_args[0][0]
        assert "--sarif" in cmd_args
        assert "--quiet" in cmd_args
        assert len(findings) == 1
        assert findings[0].rule_id == "semgrep:python.lang.security.audit.dangerous-eval"


def test_proposal1_semgrep_tools_manager_install(tmp_path: Path) -> None:
    """Proposal 1: ToolsManager reports semgrep status and installs on user request."""
    from vibetrace.tools_manager import ToolsManager

    statuses = ToolsManager.get_status(tmp_path)
    assert "semgrep" in statuses
    assert "trivy" in statuses
    assert statuses["semgrep"].engine == "Semgrep SAST CLI"

    mock_proc = MagicMock()
    mock_proc.returncode = 0
    with (
        patch("shutil.which", return_value="/usr/bin/uv"),
        patch("subprocess.run", return_value=mock_proc) as run_mock,
    ):
        ok, msg = ToolsManager.install("semgrep", tmp_path)
        assert ok is True
        assert "Semgrep CLI" in msg
        assert run_mock.called


def test_vibe_bundle_truncate_to_tokens_strict_budget() -> None:
    """Verify truncate_to_tokens strictly adheres to requested token ceiling."""
    from vibetrace.services.vibe_bundle import estimate_tokens, truncate_to_tokens

    large_text = "def compute_large():\n" + ("    value = math.sqrt(42.0) * 3.14\n" * 400)
    for budget in (64, 128, 512, 1024, 1500):
        truncated = truncate_to_tokens(large_text, budget)
        estimated = estimate_tokens(truncated)
        assert estimated <= budget, (
            f"Truncated text exceeded budget {budget}: got {estimated} tokens"
        )


def test_go_pkg_symbol_discovery_cached(tmp_path: Path) -> None:
    """Verify _find_go_symbol_file populates cache and resolves symbol without re-reading."""
    from vibetrace.indexer.parsers import ParserFactory
    from vibetrace.indexer.resolver import ModuleResolver

    go_pkg = tmp_path / "pkg"
    go_pkg.mkdir()
    (go_pkg / "alpha.go").write_text("package pkg\n\nfunc AlphaFn() {}\n", encoding="utf-8")
    (go_pkg / "beta.go").write_text("package pkg\n\nfunc BetaFn() {}\n", encoding="utf-8")

    resolver = ModuleResolver(tmp_path)
    parser = ParserFactory.get_parser("pkg/alpha.go", resolver=resolver)
    assert parser is not None

    resolved_alpha = parser._find_go_symbol_file("pkg/alpha.go", "AlphaFn")
    assert resolved_alpha.endswith("alpha.go")
    assert "pkg" in parser._go_pkg_symbol_cache

    resolved_beta = parser._find_go_symbol_file("pkg/alpha.go", "BetaFn")
    assert resolved_beta.endswith("beta.go")
