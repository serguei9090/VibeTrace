"""Tests for RegexQualityAnalyzer (S6353 character classes, S8786 ReDoS, S5843 complexity)."""

from pathlib import Path

from vibetrace.security.regex_analyzer import analyze_regex_quality


def test_regex_character_class_s6353(tmp_path: Path) -> None:
    code = """
import re

# S6353: verbose character classes
word_re = re.compile(r"^[A-Za-z0-9_]+$")
digit_re = re.compile(r"[0-9]+")
clean_re = re.compile(r"^\\w+$")
"""
    f = tmp_path / "test_regex.py"
    f.write_text(code, encoding="utf-8")

    findings = analyze_regex_quality(str(f), code)
    rule_ids = {item.rule_id for item in findings}
    assert "python:S6353" in rule_ids
    assert len([item for item in findings if item.rule_id == "python:S6353"]) == 2


def test_regex_complexity_s5843(tmp_path: Path) -> None:
    # Highly complex nested pattern (> 20 complexity)
    pattern = (
        r"([A-Za-z_][A-Za-z0-9_]*)\s*"
        r'(?:\[\s*["\']([^"\']+)["\']\s*\]|\.get\s*\(\s*["\']([^"\']+)["\']\s*(?:,\s*[^)]+)?\))'
    )
    code = f'import re\ncomplex_re = re.compile(r"""{pattern}""")\n'
    f = tmp_path / "test_complex.py"
    f.write_text(code, encoding="utf-8")

    findings = analyze_regex_quality(str(f), code)
    assert any(item.rule_id == "python:S5843" for item in findings)


def test_regex_redos_s8786_patterns(tmp_path: Path) -> None:
    dict_pat = (
        r'([A-Za-z_$][A-Za-z0-9_$]*)\s*(?:\[\s*["\']([^"\']+)["\']\s*\]'
        r'|\.get\s*\(\s*["\']([^"\']+)["\']\s*(?:,\s*[^)]+)?\))'
    )
    code = (
        "import re\n"
        f"dict_re = re.compile(r'''{dict_pat}''')\n"
        r'arrow_re = re.compile(r"\(([^)]*)\)\s*(?::\s*[^=>]+)?\s*=>")' + "\n"
        r'kw_re = re.compile(r"^([A-Za-z_][A-Za-z0-9_]*)\s*=\s*(.+)$")' + "\n"
    )
    f = tmp_path / "test_redos.py"
    f.write_text(code, encoding="utf-8")

    findings = analyze_regex_quality(str(f), code)
    redos_lines = {item.start_line for item in findings if item.rule_id == "python:S8786"}
    assert {2, 3, 4}.issubset(redos_lines)


def test_regex_dataflow_file_integration() -> None:
    dataflow_path = Path("src/vibetrace/security/dataflow.py")
    assert dataflow_path.is_file()

    findings = analyze_regex_quality(str(dataflow_path), dataflow_path.read_text(encoding="utf-8"))
    assert findings == []
