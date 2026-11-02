"""Unit tests for AST-based string literal duplication detection (RSPEC-1192)."""

from __future__ import annotations

from vibetrace.quality.literal_duplication import analyze_literal_duplications


def test_detects_duplicated_string_literals() -> None:
    code = """
def send_email(user, status):
    log("PROCESSING_TRANSACTION")
    if status == "PROCESSING_TRANSACTION":
        update("PROCESSING_TRANSACTION")
        notify("PROCESSING_TRANSACTION")
"""
    findings = analyze_literal_duplications("sample.py", code, threshold=3)
    assert len(findings) == 1
    f = findings[0]
    assert f.rule_id == "python:S1192"
    assert f.rspec_id == "RSPEC-1192"
    assert "PROCESSING_TRANSACTION" in f.title
    assert "duplicated 4 times" in f.message


def test_ignores_docstrings_and_trivial_strings() -> None:
    code = '''
"""Module docstring utf-8."""

def fn1():
    """Function docstring utf-8."""
    return "utf-8"

def fn2():
    """Another docstring utf-8."""
    return "utf-8"
'''
    findings = analyze_literal_duplications("sample.py", code, threshold=3)
    assert len(findings) == 0


def test_handles_syntax_errors_gracefully() -> None:
    code = "def bad_syntax(:"
    findings = analyze_literal_duplications("bad.py", code)
    assert findings == []
