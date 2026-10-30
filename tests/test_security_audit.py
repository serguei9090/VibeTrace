"""Unit tests for VibeTrace AST Security Scanner and Baseline Manager."""

from pathlib import Path

from vibetrace.security.ast_scanner import AstSecurityScanner
from vibetrace.security.baseline import SecurityBaselineManager


def test_command_injection_detection(tmp_path: Path):
    vuln_py = tmp_path / "vuln_shell.py"
    vuln_py.write_text(
        "import subprocess\n\n"
        "def run_cmd(user_arg):\n"
        "    subprocess.Popen(f'echo {user_arg}', shell=True)\n",
        encoding="utf-8",
    )
    findings = AstSecurityScanner.scan_file("vuln_shell.py", tmp_path)
    assert len(findings) == 1
    f = findings[0]
    assert f.cwe_id == "CWE-78"
    assert f.severity == "CRITICAL"
    assert "shell=True" in f.message


def test_eval_injection_detection(tmp_path: Path):
    vuln_py = tmp_path / "vuln_eval.py"
    vuln_py.write_text(
        "def compute(user_code):\n    return eval(user_code)\n",
        encoding="utf-8",
    )
    findings = AstSecurityScanner.scan_file("vuln_eval.py", tmp_path)
    assert len(findings) == 1
    f = findings[0]
    assert f.cwe_id == "CWE-95"
    assert f.severity == "CRITICAL"


def test_hardcoded_secrets_detection(tmp_path: Path):
    vuln_py = tmp_path / "vuln_secret.py"
    vuln_py.write_text(
        "api_key = 'sk_live_1234567890abcdef'\n",
        encoding="utf-8",
    )
    findings = AstSecurityScanner.scan_file("vuln_secret.py", tmp_path)
    assert len(findings) == 1
    f = findings[0]
    assert f.cwe_id == "CWE-798"
    assert f.severity == "HIGH"


def test_weak_crypto_detection(tmp_path: Path):
    vuln_py = tmp_path / "vuln_crypto.py"
    vuln_py.write_text(
        "import hashlib\n\ndef hash_pass(p):\n    return hashlib.md5(p.encode()).hexdigest()\n",
        encoding="utf-8",
    )
    findings = AstSecurityScanner.scan_file("vuln_crypto.py", tmp_path)
    assert len(findings) == 1
    f = findings[0]
    assert f.cwe_id == "CWE-327"
    assert f.severity == "MEDIUM"


def test_insecure_deserialization_detection(tmp_path: Path):
    vuln_py = tmp_path / "vuln_pickle.py"
    vuln_py.write_text(
        "import pickle\n\ndef load(data):\n    return pickle.loads(data)\n",
        encoding="utf-8",
    )
    findings = AstSecurityScanner.scan_file("vuln_pickle.py", tmp_path)
    assert len(findings) == 1
    f = findings[0]
    assert f.cwe_id == "CWE-502"
    assert f.severity == "HIGH"


def test_security_baseline_lifecycle(tmp_path: Path):
    vuln_py = tmp_path / "task.py"
    vuln_py.write_text("import subprocess\nsubprocess.run('ls', shell=True)\n", encoding="utf-8")

    # 1. Unapproved scan
    findings = AstSecurityScanner.scan_file("task.py", tmp_path)
    assert len(findings) == 1
    report = SecurityBaselineManager.apply_baseline_to_findings(findings, tmp_path)
    assert report.unapproved_count == 1
    assert report.findings[0].status == "UNAPPROVED"

    # 2. Record approval
    f = report.findings[0]
    SecurityBaselineManager.record_exception(
        project_root=tmp_path,
        finding_id=f.id,
        rule_id=f.rule_id,
        file_path=f.file_path,
        code_hash=f.code_hash,
        rationale="Internal build script exception",
        severity=f.severity.value,
    )

    # 3. Rescan -> APPROVED
    report2 = SecurityBaselineManager.apply_baseline_to_findings(findings, tmp_path)
    assert report2.unapproved_count == 0
    assert report2.approved_count == 1
    assert report2.findings[0].status == "APPROVED"

    # 4. Modify code -> MODIFIED_CODE
    vuln_py.write_text(
        "import subprocess\nsubprocess.run('ls -la', shell=True)\n", encoding="utf-8"
    )
    findings_mod = AstSecurityScanner.scan_file("task.py", tmp_path)
    report3 = SecurityBaselineManager.apply_baseline_to_findings(findings_mod, tmp_path)
    assert report3.modified_count == 1
    assert report3.findings[0].status == "MODIFIED_CODE"


def test_lockfile_parser_uv_and_bun(tmp_path: Path):
    from vibetrace.security.lockfile_parser import LockfileParser

    # Create dummy uv.lock
    uv_file = tmp_path / "uv.lock"
    uv_file.write_text(
        '[[package]]\nname = "vulnerable-pkg"\nversion = "1.0.0"\n', encoding="utf-8"
    )

    # Create dummy bun.lock
    web_dir = tmp_path / "web"
    web_dir.mkdir()
    bun_file = web_dir / "bun.lock"
    bun_file.write_text('{"packages": {"axios": ["axios@0.21.1", "", {}]}}', encoding="utf-8")

    packages = LockfileParser.parse_all_lockfiles(tmp_path)
    assert len(packages) == 2
    pkg_map = {p.name: p for p in packages}
    assert "vulnerable-pkg" in pkg_map
    assert pkg_map["vulnerable-pkg"].version == "1.0.0"
    assert pkg_map["vulnerable-pkg"].ecosystem == "PyPI"

    assert "axios" in pkg_map
    assert pkg_map["axios"].version == "0.21.1"
    assert pkg_map["axios"].ecosystem == "npm"


def test_osv_client_caching_and_mapping(tmp_path: Path):
    from unittest.mock import patch

    from vibetrace.indexer.db import IndexerDB
    from vibetrace.security.lockfile_parser import DependencyPackage
    from vibetrace.security.osv_client import OsvClient

    db_path = tmp_path / "test.db"
    db = IndexerDB(str(db_path))

    pkgs = [
        DependencyPackage(
            name="demo-pkg", version="1.0.0", ecosystem="PyPI", lockfile_path="uv.lock"
        )
    ]

    mock_osv_vulns = {
        ("PyPI", "demo-pkg", "1.0.0"): [
            {
                "id": "GHSA-1234-abcd",
                "summary": "Critical remote code execution vulnerability",
                "database_specific": {"severity": "CRITICAL"},
                "affected": [{"ranges": [{"events": [{"fixed": "1.0.1"}]}]}],
            }
        ]
    }

    with patch.object(OsvClient, "_fetch_from_osv", return_value=mock_osv_vulns) as mock_fetch:
        # First query -> calls OSV & caches
        findings = OsvClient.query_vulnerabilities(pkgs, db)
        assert len(findings) == 1
        assert findings[0].id == "SEC-GHSA-1234-abcd-demo-pkg-1.0.0"
        assert findings[0].severity == "CRITICAL"
        assert 'demo-pkg >= "1.0.1"' in (findings[0].compliant_snippet or "")
        assert mock_fetch.call_count == 1

        # Second query -> pulls from cache (0 new network calls)
        findings2 = OsvClient.query_vulnerabilities(pkgs, db)
        assert len(findings2) == 1
        assert findings2[0].id == findings[0].id
        assert mock_fetch.call_count == 1
