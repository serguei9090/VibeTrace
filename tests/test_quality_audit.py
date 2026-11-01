"""Unit and integration tests for VibeTrace Code Quality & Maintainability Module."""

from __future__ import annotations

import tempfile
from pathlib import Path

from vibetrace.quality.baseline import QualityBaselineManager
from vibetrace.quality.complexity import analyze_python_complexities
from vibetrace.quality.formatters import format_quality_audit_md, format_quality_audit_sarif
from vibetrace.quality.models import QualityCategory, QualitySeverity
from vibetrace.quality.service import QualityService
from vibetrace.quality.smells import analyze_python_smells


def test_cognitive_complexity_simple() -> None:
    code = """
def simple_add(a, b):
    return a + b
"""
    comps, findings = analyze_python_complexities("test.py", code, threshold=15)
    assert len(comps) == 1
    assert comps[0].cognitive_complexity == 0
    assert len(findings) == 0


def test_cognitive_complexity_nested_increments() -> None:
    code = """
def complex_fn(items):
    total = 0
    for a in items:              # +1
        if a > 0:                # +2 (nested in for)
            for b in range(a):   # +3 (nested in if in for)
                if b % 2 == 0:   # +4 (nested 3 deep)
                    total += b
    return total
"""
    comps, findings = analyze_python_complexities("test.py", code, threshold=5)
    assert len(comps) == 1
    # 1 (for) + 2 (if) + 3 (for) + 4 (if) = 10
    assert comps[0].cognitive_complexity == 10
    assert len(findings) == 1
    assert findings[0].rule_id == "python:S3776"
    assert findings[0].severity == QualitySeverity.HIGH
    assert findings[0].category == QualityCategory.COMPLEXITY


def test_smell_mergeable_if_s1066() -> None:
    code = """
def check_file(file):
    if file.is_valid():
        if file.is_file():
            process(file)
"""
    findings = analyze_python_smells("test.py", code)
    s1066 = [f for f in findings if f.rule_id == "python:S1066"]
    assert len(s1066) == 1
    assert s1066[0].rule_id == "python:S1066"
    assert s1066[0].severity == QualitySeverity.MEDIUM
    assert "Merge this" in s1066[0].title


def test_smell_deep_nesting_s134() -> None:
    code = """
def deeply_nested():
    if True:
        for x in [1]:
            while True:
                try:
                    if False:
                        pass
                except Exception:
                    pass
                break
"""
    findings = analyze_python_smells("test.py", code)
    s134 = [f for f in findings if f.rule_id == "python:S134"]
    assert len(s134) >= 1
    assert s134[0].rule_id == "python:S134"


def test_smell_too_many_params_s107() -> None:
    code = """
def many_params(a, b, c, d, e, f, g, h):
    return a
"""
    findings = analyze_python_smells("test.py", code)
    s107 = [f for f in findings if f.rule_id == "python:S107"]
    assert len(s107) == 1
    assert s107[0].rule_id == "python:S107"
    assert s107[0].metric_value == 8.0


def test_smell_empty_function_s1186() -> None:
    code = """
def empty_no_doc():
    pass

def empty_with_doc():
    \"\"\"Allowed because it has documentation.\"\"\"
    pass
"""
    findings = analyze_python_smells("test.py", code)
    s1186 = [f for f in findings if f.rule_id == "python:S1186"]
    assert len(s1186) == 1
    assert s1186[0].symbol_name == "empty_no_doc"


def test_quality_baseline_and_hash_drift() -> None:
    with tempfile.TemporaryDirectory() as tmpdir:
        root = Path(tmpdir)
        finding_id = "QUAL-S1066-abc123"

        # Record approval
        QualityBaselineManager.record_exception(
            project_root=root,
            finding_id=finding_id,
            rule_id="python:S1066",
            file_path="src/app.py",
            code_hash="original_hash",
            rationale="Approved legacy structure",
            symbol_name="check_file",
        )

        # Baseline file created
        baseline_file = root / ".vibetrace" / "quality.toml"
        assert baseline_file.is_file()

        # Test finding matching hash -> APPROVED
        from vibetrace.quality.models import QualityFinding

        f1 = QualityFinding(
            id=finding_id,
            rule_id="python:S1066",
            rspec_id="RSPEC-1066",
            category=QualityCategory.CODE_SMELL,
            severity=QualitySeverity.MEDIUM,
            title="Merge this if",
            message="Mergeable if",
            file_path="src/app.py",
            code_hash="original_hash",
            symbol_name="check_file",
        )
        report = QualityBaselineManager.apply_baseline_to_findings([f1], root)
        assert report.approved_count == 1
        assert report.findings[0].status == "APPROVED"

        # Test finding with modified hash -> MODIFIED_CODE
        f2 = QualityFinding(
            id=finding_id,
            rule_id="python:S1066",
            rspec_id="RSPEC-1066",
            category=QualityCategory.CODE_SMELL,
            severity=QualitySeverity.MEDIUM,
            title="Merge this if",
            message="Mergeable if",
            file_path="src/app.py",
            code_hash="tampered_modified_hash",
            symbol_name="check_file",
        )
        report2 = QualityBaselineManager.apply_baseline_to_findings([f2], root)
        assert report2.modified_count == 1
        assert report2.findings[0].status == "MODIFIED_CODE"


def test_quality_service_and_formatters() -> None:
    with tempfile.TemporaryDirectory() as tmpdir:
        root = Path(tmpdir)
        src_dir = root / "src"
        src_dir.mkdir(parents=True)
        py_file = src_dir / "sample.py"
        py_file.write_text(
            """
def complex_fn(a, b, c, d, e, f, g, h):
    if a:
        if b:
            return c
    return 0
""",
            encoding="utf-8",
        )

        report = QualityService.audit_project(
            db=None,
            project_root=root,
            complexity_threshold=1,
        )

        assert (
            report.total_findings >= 2
        )  # S107 (8 params) + S1066 (mergeable if) + S3776 (complexity > 1)
        md = format_quality_audit_md(report)
        assert "[QUALITY]" in md
        assert "Total Findings:" in md

        sarif = format_quality_audit_sarif(report)
        assert "VibeTrace-Quality" in sarif
        assert "sarif-2.1.0.json" in sarif


def test_exact_sonarqube_issue_reproduction() -> None:
    """Verifies that the exact SonarQube findings reported on cli_audit.py are caught."""
    original_snippet = """
def cmd_audit_security_legacy(args):
    is_strict = True
    fail_on = "HIGH"
    unapproved_medium = [1]
    has_failure = False

    if False:
        pass
    elif is_strict or fail_on in ("MEDIUM", "LOW"):
        if unapproved_medium:
            has_failure = True
    return has_failure
"""
    smell_findings = analyze_python_smells("cli_audit.py", original_snippet)
    s1066 = [f for f in smell_findings if f.rule_id == "python:S1066"]
    assert len(s1066) == 1
    assert s1066[0].rule_id == "python:S1066"
    assert "Merge this" in s1066[0].title


def test_inline_comment_suppression() -> None:
    with tempfile.TemporaryDirectory() as tmpdir:
        root = Path(tmpdir)
        src = root / "src"
        src.mkdir()
        py_file = src / "suppressed.py"
        py_file.write_text(
            """
def check_stuff(file):
    # vibetrace:ignore[S1066]: Intentional condition separation # NOSONAR
    if file.is_valid():
        if file.is_file():
            return True
    return False
""",
            encoding="utf-8",
        )

        report = QualityService.audit_project(db=None, project_root=root)
        # Should be detected as finding, but status should be APPROVED (not UNAPPROVED)
        assert report.unapproved_count == 0
        assert report.approved_count == 1
        assert report.findings[0].tag == "inline-comment"


def test_baseline_triage_index_and_subsequent_skipping() -> None:
    with tempfile.TemporaryDirectory() as tmpdir:
        root = Path(tmpdir)
        src = root / "src"
        src.mkdir()
        py_file = src / "auto.py"
        py_file.write_text(
            """
def check_stuff(file):
    if file.is_valid():
        if file.is_file():
            return True
    return False
""",
            encoding="utf-8",
        )

        # 1. Audit finds 1 unapproved finding
        report1 = QualityService.audit_project(db=None, project_root=root)
        assert report1.unapproved_count == 1
        finding = report1.findings[0]

        # 2. Triage & record into catalog (like audit splits and security)
        QualityService.record_exception(
            project_root=root,
            finding_id=finding.id,
            rule_id=finding.rule_id,
            file_path=finding.file_path,
            code_hash=finding.code_hash,
            rationale="Intentional fast path checked in review",
            tag="intentional",
            status="APPROVED",
        )

        # 3. Next audit run silently skips it because it matches the catalog
        report2 = QualityService.audit_project(db=None, project_root=root)
        assert report2.unapproved_count == 0
        assert report2.approved_count == 1
        assert report2.findings[0].status == "APPROVED"
        assert report2.findings[0].rationale == "Intentional fast path checked in review"
        assert report2.approved_count == 1


def test_mutable_default_s5714_and_b006_alias() -> None:
    code = """
def bad_fn(x, items=[]):
    items.append(x)
    return items

def good_fn(x, items=None):
    if items is None:
        items = []
    return items
"""
    findings = analyze_python_smells("test.py", code)
    s5714 = [f for f in findings if f.rule_id == "python:S5714"]
    assert len(s5714) == 1
    assert s5714[0].severity == QualitySeverity.HIGH
    assert s5714[0].category == QualityCategory.RELIABILITY


def test_self_comparison_s1764() -> None:
    code = """
def compare_things(x, y):
    if x == x:
        return 0
    return 1
"""
    findings = analyze_python_smells("test.py", code)
    s1764 = [f for f in findings if f.rule_id == "python:S1764"]
    assert len(s1764) == 1
    assert "Identical expressions" in s1764[0].title


def test_unreachable_code_s1763() -> None:
    code = """
def jumpy():
    return 42
    print("unreachable!")
"""
    findings = analyze_python_smells("test.py", code)
    s1763 = [f for f in findings if f.rule_id == "python:S1763"]
    assert len(s1763) == 1
    assert "terminal jump" in s1763[0].title


def test_duplicate_dict_key_s5712() -> None:
    code = """
config = {
    "host": "localhost",
    "port": 8080,
    "host": "0.0.0.0"
}
"""
    findings = analyze_python_smells("test.py", code)
    s5712 = [f for f in findings if f.rule_id == "python:S5712"]
    assert len(s5712) == 1
    assert "duplicate key 'host'" in s5712[0].title


def test_cross_ecosystem_alias_suppression() -> None:
    with tempfile.TemporaryDirectory() as tmpdir:
        root = Path(tmpdir)
        src = root / "src"
        src.mkdir()
        py_file = src / "vendor_compat.py"
        py_file.write_text(
            """
# Test Flake8-Bugbear B006 alias suppressing Sonar S5714
# noqa: B006
def append_things(val, bag=[]):
    return val

# Test Flake8-Simplify SIM102 alias suppressing Sonar S1066
def nested(a, b):
    # noqa: SIM102
    if a:
        if b:
            return 1
    return 0
""",
            encoding="utf-8",
        )

        report = QualityService.audit_project(db=None, project_root=root)
        # Both should be suppressed via their vendor aliases!
        assert report.unapproved_count == 0
        assert report.approved_count == 2


def test_css_quality_analyzer_rules() -> None:
    from vibetrace.quality.css_analyzer import analyze_css_quality

    css_code = """
.input {
  color: white;
  background: rgba(255, 255, 255, 0.05);
  transition: all 0.2s ease;
}

.button {
  transition:
    transform 0.1s ease,
    opacity 0.2s ease;
}

.spinner {
  animation: spin 1s infinite;
}

@media (prefers-reduced-motion: no-preference) {
  .guarded-spinner {
    animation: spin 1s infinite;
  }
}

.override {
  background: red !important;
}
"""
    findings = analyze_css_quality("styles.css", css_code)
    rule_map = {f.rule_id: f for f in findings}

    assert "css:S7924" in rule_map  # Contrast
    assert "css:S9449" in rule_map  # Transition: all
    assert "css:S9440" in rule_map  # Reduced motion
    assert "css:S9423" in rule_map  # !important

    # Guarded spinner inside prefers-reduced-motion should not be flagged
    guarded = [f for f in findings if f.symbol_name == ".guarded-spinner"]
    assert len(guarded) == 0


def test_css_project_audit_integration() -> None:
    with tempfile.TemporaryDirectory() as tmpdir:
        root = Path(tmpdir)
        src = root / "src"
        src.mkdir()
        css_file = src / "test.css"
        css_file.write_text(
            """
.box {
  width: 100px !important;
  transition: all 0.3s;
}
""",
            encoding="utf-8",
        )

        report = QualityService.audit_project(db=None, project_root=root)
        assert report.total_findings == 2
        rule_ids = {f.rule_id for f in report.findings}
        assert "css:S9423" in rule_ids
        assert "css:S9449" in rule_ids


def test_typescript_and_jsx_quality_analyzer() -> None:
    from vibetrace.quality.ts_analyzer import analyze_ts_quality

    tsx_code = """
import React from 'react';

// ts:S4323: explicit any
const config: any = { mode: 'debug' };
const payload = data as any;

// ts:S6544: unhandled floating promise
fetch('/api/sync').then(r => r.json());

export function UserCard() {
  return (
    <div>
      {/* jsx:S6852: img missing alt */}
      <img src="/avatar.png" />

      {/* jsx:S6848: non-interactive element with onClick */}
      <div onClick={() => console.log('clicked')}>
        Click Me
      </div>

      {/* Compliant button with click handler */}
      <button type="button" onClick={() => console.log('ok')}>
        Compliant
      </button>

      {/* Compliant img with alt */}
      <img src="/logo.png" alt="Company Logo" />
    </div>
  );
}
"""
    findings = analyze_ts_quality("UserCard.tsx", tsx_code)
    rule_ids = {f.rule_id for f in findings}

    assert "ts:S4323" in rule_ids  # explicit any
    assert "ts:S6544" in rule_ids  # floating promise
    assert "jsx:S6852" in rule_ids  # img missing alt
    assert "jsx:S6848" in rule_ids  # div onClick without role/key

    # Compliant button and compliant img should not be flagged
    img_findings = [f for f in findings if f.rule_id == "jsx:S6852"]
    assert len(img_findings) == 1  # Only the uncompliant img


def test_dockerfile_quality_analyzer() -> None:
    from vibetrace.quality.docker_analyzer import analyze_docker_quality

    dockerfile_code = """
FROM python:3.12-slim

# docker:S6506: relative workdir
WORKDIR app

# docker:S6505: ADD used for local files
ADD ./src /app/src

# docker:S6504: duplicate CMD in same stage
CMD ["python", "server.py"]
CMD ["python", "main.py"]

# Valid remote ADD (should NOT be flagged)
ADD https://example.com/archive.tar.gz /tmp/archive.tar.gz
"""
    findings = analyze_docker_quality("Dockerfile", dockerfile_code)
    rule_ids = {f.rule_id for f in findings}

    assert "docker:S6506" in rule_ids  # relative workdir
    assert "docker:S6505" in rule_ids  # ADD local file
    assert "docker:S6504" in rule_ids  # duplicate CMD

    # Remote ADD should not be flagged as S6505
    s6505_findings = [f for f in findings if f.rule_id == "docker:S6505"]
    assert len(s6505_findings) == 1  # only local ADD


def test_s3358_nested_ternary_detector() -> None:
    from vibetrace.quality.smells import analyze_python_smells

    code = """
def format_icon(cg):
    icon = "✅" if cg.ok else ("❌" if cg.enforced else "⚠️")
    simple = "yes" if cg.ok else "no"
    return icon, simple
"""
    findings = analyze_python_smells("test.py", code)
    s3358_findings = [f for f in findings if f.rule_id == "python:S3358"]
    assert len(s3358_findings) == 1
    assert s3358_findings[0].rspec_id == "RSPEC-3358"
    assert s3358_findings[0].start_line == 3


def test_quality_rule_sync_and_catalog_caching() -> None:
    from vibetrace.indexer.db import IndexerDB
    from vibetrace.quality.rule_sync import QualityRuleCatalogManager
    from vibetrace.quality.rules import get_rule_spec

    db = IndexerDB(":memory:")

    # 1. First sync loads from bundled JSON into SQLite
    res1 = QualityRuleCatalogManager.sync_catalog(db=db)
    assert res1.source == "bundled"
    assert res1.rules_loaded >= 1300
    assert "py" in res1.languages
    assert "ts" in res1.languages

    # 2. Second sync hits local SQLite cache
    res2 = QualityRuleCatalogManager.sync_catalog(db=db)
    assert res2.source == "cache"
    assert res2.rules_loaded == res1.rules_loaded

    # 3. Rule spec resolution works for synced rules
    spec = get_rule_spec("typescript:S2589")
    assert spec is not None
    assert spec.rule_id == "typescript:S2589"
    assert "gratuitous" in spec.title.lower() or "boolean" in spec.title.lower()


