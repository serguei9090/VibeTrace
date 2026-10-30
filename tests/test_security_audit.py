"""Unit tests for VibeTrace AST Security Scanner and Baseline Manager."""

from pathlib import Path

import pytest

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


def test_security_api_endpoints(tmp_path: Path):
    from unittest.mock import patch

    from fastapi.testclient import TestClient

    from vibetrace.indexer.db import IndexerDB
    from vibetrace.server_api import app

    db_path = tmp_path / "test.db"
    db = IndexerDB(str(db_path))

    # Add a mock file with a vulnerable line
    vuln_py = tmp_path / "cmd_runner.py"
    vuln_py.write_text("import os\ndef run(x):\n    os.system(x)\n", encoding="utf-8")

    from vibetrace.indexer.models import Node

    db.upsert_node(
        Node(
            id="cmd_run",
            name="run",
            qualified_name="run",
            kind="function",
            file_path="cmd_runner.py",
            start_line=2,
            end_line=3,
        )
    )

    with (
        patch("vibetrace.server_api.db", db),
        patch("vibetrace.server_api.project_root", tmp_path),
    ):
        client = TestClient(app)

        # 1. GET /api/security/audit (skip dependencies to isolate AST)
        resp = client.get("/api/security/audit?skip_deps=true")
        assert resp.status_code == 200
        data = resp.json()
        assert data["total_findings"] >= 1
        assert data["unapproved_count"] >= 1
        finding = data["findings"][0]

        # 2. POST /api/security/approve
        approve_payload = {
            "finding_id": finding["id"],
            "rule_id": finding["rule_id"],
            "file_path": finding["file_path"],
            "code_hash": finding["code_hash"],
            "rationale": "Test environment approval",
            "severity": finding["severity"],
            "status": "APPROVED",
            "reviewed_by": "test-suite",
        }
        resp_post = client.post("/api/security/approve", json=approve_payload)
        assert resp_post.status_code == 200
        assert resp_post.json()["status"] == "ok"

        # 3. Rescan -> should be approved
        resp_after = client.get("/api/security/audit?skip_deps=true")
        assert resp_after.status_code == 200
        data_after = resp_after.json()
        assert data_after["approved_count"] == 1
        assert data_after["unapproved_count"] == 0


@pytest.mark.asyncio
async def test_mcp_security_tools(tmp_path: Path):
    from vibetrace.mcp.schemas import (
        ApproveSecurityExceptionInput,
        AuditSecurityInput,
        ResponseFormat,
    )
    from vibetrace.mcp.server import (
        vibetrace_approve_security_exception,
        vibetrace_audit_security,
    )

    vuln_py = tmp_path / "script.py"
    vuln_py.write_text("eval('1+1')\n", encoding="utf-8")

    from vibetrace.indexer.db import IndexerDB
    from vibetrace.indexer.models import Node

    context_dir = tmp_path / ".vibetrace"
    context_dir.mkdir(parents=True, exist_ok=True)
    db = IndexerDB(str(context_dir / "vibetrace.db"))
    db.upsert_node(
        Node(
            id="n_eval",
            name="eval_test",
            qualified_name="eval_test",
            kind="function",
            file_path="script.py",
            start_line=1,
            end_line=1,
        )
    )

    # 1. MCP Audit
    audit_res = await vibetrace_audit_security(
        AuditSecurityInput(
            scope_path=str(tmp_path),
            skip_deps=True,
            response_format=ResponseFormat.JSON,
        )
    )
    import json

    data = json.loads(audit_res)
    assert data["total_findings"] >= 1
    f = data["findings"][0]

    # 2. MCP Approve
    approve_res = await vibetrace_approve_security_exception(
        ApproveSecurityExceptionInput(
            finding_id=f["id"],
            rule_id=f["rule_id"],
            file_path=f["file_path"],
            code_hash=f["code_hash"],
            rationale="MCP approved rule exception",
            scope_path=str(tmp_path),
            response_format=ResponseFormat.JSON,
        )
    )
    appr_data = json.loads(approve_res)
    assert appr_data["status"] == "ok"
    assert appr_data["finding_id"] == f["id"]


def test_js_ts_injection_rules(tmp_path: Path):
    (tmp_path / "a.ts").write_text(
        "import cp from 'child_process';\n"
        "export function run(x: string) {\n"
        "  cp.execSync(x);\n"
        "  eval(x);\n"
        "  return new Function(x);\n"
        "}\n",
        encoding="utf-8",
    )
    (tmp_path / "b.tsx").write_text("export const f = (x: string) => eval(x);\n", encoding="utf-8")
    cwes = sorted(f.cwe_id for f in AstSecurityScanner.scan_file("a.ts", tmp_path))
    assert cwes == ["CWE-78", "CWE-95", "CWE-95"]
    assert [f.cwe_id for f in AstSecurityScanner.scan_file("b.tsx", tmp_path)] == ["CWE-95"]


def test_sql_injection_and_path_traversal_detection(tmp_path: Path):
    (tmp_path / "db_io.py").write_text(
        "def query_user(cur, uid, fname):\n"
        "    cur.execute(f'SELECT * FROM users WHERE id = {uid}')\n"
        "    return open(f'/var/uploads/{fname}', 'rb').read()\n",
        encoding="utf-8",
    )
    findings = AstSecurityScanner.scan_file("db_io.py", tmp_path)
    cwes = sorted(f.cwe_id for f in findings)
    assert cwes == ["CWE-22", "CWE-89"]


def test_poetry_and_requirements_lockfiles(tmp_path: Path):
    from vibetrace.security.lockfile_parser import LockfileParser

    (tmp_path / "poetry.lock").write_text(
        '[[package]]\nname = "flask"\nversion = "3.0.0"\n', encoding="utf-8"
    )
    (tmp_path / "requirements.txt").write_text(
        "# comment\ndjango==5.0.1\nrequests>=2.0\n", encoding="utf-8"
    )
    pkgs = LockfileParser.parse_all_lockfiles(tmp_path)
    names = sorted((p.name, p.version, p.ecosystem) for p in pkgs)
    assert ("django", "5.0.1", "PyPI") in names
    assert ("flask", "3.0.0", "PyPI") in names


@pytest.mark.asyncio
async def test_remediate_vulnerability_and_baseline_metadata(tmp_path: Path):
    import json

    from vibetrace.indexer.db import IndexerDB
    from vibetrace.indexer.models import Node
    from vibetrace.mcp.schemas import RemediateVulnerabilityInput, ResponseFormat
    from vibetrace.mcp.server import vibetrace_remediate_vulnerability

    (tmp_path / "s.py").write_text("eval('1')\n", encoding="utf-8")
    ctx = tmp_path / ".vibetrace"
    ctx.mkdir(parents=True, exist_ok=True)
    db = IndexerDB(str(ctx / "vibetrace.db"))
    db.upsert_node(
        Node(
            id="n1",
            name="f1",
            qualified_name="f1",
            kind="function",
            file_path="s.py",
            start_line=1,
            end_line=1,
        )
    )
    res = await vibetrace_remediate_vulnerability(
        RemediateVulnerabilityInput(scope_path=str(tmp_path), response_format=ResponseFormat.JSON)
    )
    bundle = json.loads(res)
    assert bundle["total_remediations"] == 1
    assert "CWE-95" in bundle["remediations"][0]["ai_fix_prompt"]

    SecurityBaselineManager.record_exception(
        project_root=tmp_path,
        finding_id=bundle["remediations"][0]["finding_id"],
        rule_id="py:CWE-95",
        file_path="s.py",
        code_hash="abc",
        rationale="test",
        category="taint_reachability",
        symbol_name="f1",
    )
    toml_text = (ctx / "security.toml").read_text(encoding="utf-8")
    assert 'category = "taint_reachability"' in toml_text
    assert 'symbol_name = "f1"' in toml_text


def test_nosec_and_false_positive_precision(tmp_path: Path):
    (tmp_path / "prec.py").write_text(
        "import hashlib, os\n"
        "def f(conn, placeholders, x):\n"
        "    os.system(x)  # nosec\n"
        "    hashlib.md5(b'abc', usedforsecurity=False)\n"
        "    conn.execute(f'DELETE FROM t WHERE id IN ({placeholders})')\n",
        encoding="utf-8",
    )
    assert AstSecurityScanner.scan_file("prec.py", tmp_path) == []

    (tmp_path / "static.ts").write_text(
        "import cp from 'child_process';\n"
        "cp.execSync(process.platform === 'win32' ? 'where uv' : 'which uv');\n"
        "cp.execSync(`kill ${pid}`);\n",
        encoding="utf-8",
    )
    ts_findings = AstSecurityScanner.scan_file("static.ts", tmp_path)
    assert len(ts_findings) == 1
    assert ts_findings[0].start_line == 3
    assert "execFileSync" in (ts_findings[0].compliant_snippet or "")


def test_go_and_rust_security_rules(tmp_path: Path):
    (tmp_path / "main.go").write_text(
        "package main\n"
        "import (\n"
        '  "crypto/md5"\n'
        '  "os/exec"\n'
        ")\n"
        "func run(u string) {\n"
        '  exec.Command("sh", "-c", u)\n'
        "  md5.New()\n"
        "}\n",
        encoding="utf-8",
    )
    go_cwes = sorted(f.cwe_id for f in AstSecurityScanner.scan_file("main.go", tmp_path))
    assert go_cwes == ["CWE-327", "CWE-78"]

    (tmp_path / "main.rs").write_text(
        "use std::process::Command;\n"
        "fn run(u: &str) {\n"
        '    Command::new("sh").arg("-c").arg(u).status();\n'
        "    Md5::new();\n"
        "}\n",
        encoding="utf-8",
    )
    rs_cwes = sorted(f.cwe_id for f in AstSecurityScanner.scan_file("main.rs", tmp_path))
    assert rs_cwes == ["CWE-327", "CWE-78"]


def test_sarif_export_and_cli_approve(tmp_path: Path):
    import argparse
    import json

    from vibetrace.cli_audit import cmd_audit_security
    from vibetrace.indexer.db import IndexerDB
    from vibetrace.indexer.models import Node
    from vibetrace.security.formatters import format_security_audit_sarif
    from vibetrace.security.service import SecurityService

    (tmp_path / "app.py").write_text("eval('2+2')\n", encoding="utf-8")
    ctx = tmp_path / ".vibetrace"
    ctx.mkdir(parents=True, exist_ok=True)
    db = IndexerDB(str(ctx / "vibetrace.db"))
    db.upsert_node(
        Node(
            id="a1",
            name="app_fn",
            qualified_name="app_fn",
            kind="function",
            file_path="app.py",
            start_line=1,
            end_line=1,
        )
    )
    report = SecurityService.audit_project(db, tmp_path, check_dependencies=False)
    sarif = json.loads(format_security_audit_sarif(report))
    assert sarif["version"] == "2.1.0"
    assert sarif["runs"][0]["results"][0]["ruleId"] == "CWE-95"
    assert sarif["runs"][0]["results"][0]["level"] == "error"

    rc = cmd_audit_security(
        argparse.Namespace(
            path=str(tmp_path),
            file=None,
            skip_deps=True,
            update=False,
            no_taint=True,
            include_tests=False,
            approve=True,
            rationale="CLI approved",
            auto_sync=False,
            root=str(tmp_path),
        )
    )
    assert rc == 0
    report2 = SecurityService.audit_project(db, tmp_path, check_dependencies=False)
    assert report2.approved_count == 1
    assert report2.unapproved_count == 0
