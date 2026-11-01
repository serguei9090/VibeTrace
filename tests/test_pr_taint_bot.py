"""Tests for Automated PR Taint Bot & Inline Remediation Generator."""

import argparse
from pathlib import Path

from vibetrace.cli_audit import cmd_audit_pr_taint
from vibetrace.security.models import FindingCategory, SecurityFinding, Severity
from vibetrace.security.pr_bot import (
    PRTaintBot,
    PRTaintComment,
    PRTaintReport,
    build_comment_body,
    synthesize_remediation,
)


def test_synthesize_remediation_sql_injection():
    snippet = "cursor.execute(f\"SELECT * FROM users WHERE username = '{username}'\")"
    finding = SecurityFinding(
        id="SEC-CWE-89-123456789012",
        rule_id="python:CWE-89",
        cwe_id="CWE-89",
        category=FindingCategory.TAINT_REACHABILITY,
        severity=Severity.HIGH,
        title="SQL Injection",
        message="User input concatenated into SQL query",
        file_path="src/routes/users.py",
        start_line=25,
        end_line=25,
        code_hash="abcdef123456",
        non_compliant_snippet=snippet,
    )
    remediation = synthesize_remediation(finding)
    assert remediation is not None
    assert 'cursor.execute("SELECT * FROM users WHERE username = %s", (username,))' in remediation


def test_synthesize_remediation_sql_multi_var():
    snippet = "db.execute(f\"SELECT * FROM items WHERE cat = '{cat}' AND price < {max_price}\")"
    finding = SecurityFinding(
        id="SEC-CWE-89-abcdef123456",
        rule_id="python:CWE-89",
        cwe_id="CWE-89",
        category=FindingCategory.TAINT_REACHABILITY,
        severity=Severity.HIGH,
        title="SQL Injection",
        message="SQL injection",
        file_path="src/items.py",
        start_line=10,
        end_line=10,
        code_hash="123456abcdef",
        non_compliant_snippet=snippet,
    )
    remediation = synthesize_remediation(finding)
    assert remediation is not None
    expected_replacement = (
        'db.execute("SELECT * FROM items WHERE cat = %s AND price < %s", (cat, max_price))'
    )
    assert expected_replacement in remediation


def test_synthesize_remediation_command_injection():
    snippet = 'subprocess.run(f"tar -czf {out} {src}", shell=True)'
    finding = SecurityFinding(
        id="SEC-CWE-78-123456789012",
        rule_id="python:CWE-78",
        cwe_id="CWE-78",
        category=FindingCategory.TAINT_REACHABILITY,
        severity=Severity.HIGH,
        title="Command Injection",
        message="Command execution with shell=True",
        file_path="src/archive.py",
        start_line=40,
        end_line=40,
        code_hash="abcdef123456",
        non_compliant_snippet=snippet,
    )
    remediation = synthesize_remediation(finding)
    assert remediation is not None
    assert "shell=False" in remediation


def test_build_comment_body():
    finding = SecurityFinding(
        id="SEC-CWE-89-123456789012",
        rule_id="python:CWE-89",
        cwe_id="CWE-89",
        category=FindingCategory.TAINT_REACHABILITY,
        severity=Severity.HIGH,
        title="SQL Injection",
        message="Direct query concatenation",
        file_path="src/db.py",
        start_line=15,
        end_line=15,
        code_hash="hash123",
        non_compliant_snippet='cursor.execute(f"SELECT * FROM u WHERE id={id}")',
        metadata={
            "taint_path": ["api_handler", "user_service", "raw_query"],
            "taint_vars": ["id", "query_id"],
        },
    )
    suggestion = 'cursor.execute("SELECT * FROM u WHERE id=%s", (id,))'
    body = build_comment_body(finding, suggestion)

    assert "### 🛡️ VibeTrace Security Alert: SQL Injection (`CWE-89`)" in body
    assert "`api_handler` `→` `user_service` `→` `raw_query`" in body
    assert "- **Tainted Variables:** `id`, `query_id`" in body
    assert "```suggestion" in body
    assert 'cursor.execute("SELECT * FROM u WHERE id=%s", (id,))' in body
    assert "Commit suggestion" in body


def test_apply_remediations_local(tmp_path: Path):
    source_file = tmp_path / "query.py"
    vuln_code = (
        "def run_query(username):\n"
        "    cursor.execute(f\"SELECT * FROM users WHERE name = '{username}'\")\n"
        "    return cursor.fetchall()\n"
    )
    source_file.write_text(vuln_code, encoding="utf-8")

    comment = PRTaintComment(
        file_path="query.py",
        start_line=2,
        end_line=2,
        cwe_id="CWE-89",
        title="SQL Injection",
        severity="HIGH",
        non_compliant_snippet="cursor.execute(f\"SELECT * FROM users WHERE name = '{username}'\")",
        suggested_snippet='cursor.execute("SELECT * FROM users WHERE name = %s", (username,))',
        comment_body="Fix SQL injection",
    )
    report = PRTaintReport(
        changed_files=["query.py"],
        total_findings=1,
        comments=[comment],
    )

    applied = PRTaintBot.apply_remediations(tmp_path, report)
    assert applied == 1

    updated_content = source_file.read_text(encoding="utf-8")
    assert 'cursor.execute("SELECT * FROM users WHERE name = %s", (username,))' in updated_content
    assert 'f"SELECT' not in updated_content


def test_github_review_payload_missing_token():
    report = PRTaintReport(
        pr_number=42,
        changed_files=["app.py"],
        total_findings=0,
    )
    res = PRTaintBot.post_github_review(report, token=None, repo=None)
    assert res["success"] is False
    assert "Missing GITHUB_TOKEN" in res["error"]


def test_cli_audit_pr_taint_clean_run(tmp_path: Path):
    (tmp_path / "pyproject.toml").write_text(
        "[project]\nname='test'\nversion='0.1.0'\n",
        encoding="utf-8",
    )
    args = argparse.Namespace(
        path=str(tmp_path),
        base="main",
        staged=False,
        pr=None,
        post=False,
        apply=False,
        output_md=str(tmp_path / "summary.md"),
        json=True,
        no_sync=True,
    )
    code = cmd_audit_pr_taint(args)
    assert code == 0
    assert (tmp_path / "summary.md").is_file()
