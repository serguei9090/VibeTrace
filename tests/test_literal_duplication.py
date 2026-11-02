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


def test_ignores_dictionary_keys_and_type_annotations() -> None:
    code = """
__all__ = ["field_name", "category_tag"]

def schema_one(val: "field_name") -> "category_tag":
    return {
        "field_name": 1,
        "category_tag": 2,
    }

def schema_two() -> dict:
    return {
        "field_name": 10,
        "category_tag": 20,
    }

def schema_three() -> dict:
    return {
        "field_name": 100,
        "category_tag": 200,
    }
"""
    findings = analyze_literal_duplications("schema.py", code, threshold=3)
    assert len(findings) == 0


def test_ignores_short_strings_under_five_chars() -> None:
    code = """
def route():
    a = "GET"
    b = "GET"
    c = "GET"
    d = "GET"
"""
    findings = analyze_literal_duplications("route.py", code, threshold=3)
    assert len(findings) == 0
