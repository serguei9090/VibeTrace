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
