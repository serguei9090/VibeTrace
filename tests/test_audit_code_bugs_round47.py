"""Stress Fuzzing & Structural Edge-Case Test Suite (Audit Round 47).

Covers zero-crash resilience across all 10 AST parsers:
1. Malformed byte streams (binary garbage, null bytes, truncated strings).
2. Deeply nested syntax trees (nested brackets, parentheses 120+ deep).
3. Unicode edge cases (RTL text, zero-width joiners, emoji identifiers).
4. Unterminated constructs (unclosed quotes, unclosed multiline comments).
5. Cross-platform path invariant fuzzing (Windows backslashes, mixed slashes).
"""

from __future__ import annotations

import random

import pytest

from vibetrace.indexer.db_queries import _clean_path
from vibetrace.indexer.parsers.generic_parser import GenericParser
from vibetrace.indexer.parsers.go_parser import GoParser
from vibetrace.indexer.parsers.js_parser import JavaScriptParser
from vibetrace.indexer.parsers.python_parser import PythonParser
from vibetrace.indexer.parsers.rust_parser import RustParser
from vibetrace.indexer.parsers.ts_parser import TypeScriptParser


@pytest.fixture
def all_parsers():
    """Return an instance of each language parser."""
    return [
        (PythonParser(), "test.py"),
        (JavaScriptParser(), "test.js"),
        (TypeScriptParser(), "test.ts"),
        (GoParser(), "test.go"),
        (RustParser(), "test.rs"),
        (GenericParser("java"), "Test.java"),
        (GenericParser("cpp"), "test.cpp"),
        (GenericParser("c_sharp"), "Test.cs"),
        (GenericParser("ruby"), "test.rb"),
        (GenericParser("php"), "test.php"),
    ]


def test_parsers_resilience_on_random_binary_fuzz(all_parsers):
    """Verify that completely random byte streams and binary fuzz never crash any parser."""
    random.seed(42)

    # Generate 25 distinct corrupt/malformed byte sequences
    fuzz_samples: list[bytes] = [
        b"\x00" * 50,
        b"\xff\xfe\xfd\x00\x01\x02" * 10,
        b"".join(bytes([random.randint(0, 255)]) for _ in range(500)),
        b"def \x00\x01(a, \xff): \xfe\n",
        b"class \x80\x81\x82 { int \x00; }\n",
        b"func \x00() { return \xff }\n",
        b"fn \x00() -> \xff { }\n",
    ]

    for _ in range(18):
        length = random.randint(10, 300)
        chunk = bytearray(random.getrandbits(8) for _ in range(length))
        fuzz_samples.append(bytes(chunk))

    for parser, filename in all_parsers:
        p_name = type(parser).__name__
        for idx, sample in enumerate(fuzz_samples):
            try:
                nodes, edges = parser.extract(filename, sample)
                assert isinstance(nodes, list)
                assert isinstance(edges, list)
            except Exception as err:
                pytest.fail(f"{p_name} crashed on fuzz #{idx} for {filename}: {err}")


def test_parsers_resilience_on_deeply_nested_trees(all_parsers):
    """Verify parsers handle deeply nested structures (depth 120) without stack overflow."""
    depth = 120

    nested_python = ("(" * depth) + "1" + (")" * depth)
    nested_braces = ("{" * depth) + "x = 1;" + ("}" * depth)
    nested_brackets = ("[" * depth) + "'val'" + ("]" * depth)

    samples = [
        nested_python.encode("utf-8"),
        nested_braces.encode("utf-8"),
        nested_brackets.encode("utf-8"),
    ]

    for parser, filename in all_parsers:
        for s in samples:
            nodes, edges = parser.extract(filename, s)
            assert isinstance(nodes, list)
            assert isinstance(edges, list)


def test_parsers_resilience_on_unicode_and_emojis(all_parsers):
    """Verify parsers gracefully parse Unicode identifiers, emojis, RTL characters."""
    unicode_code = """
# 🚀 Unicode Stress Test
class 宇宙Class:
    def 계산_함수(self, פונקציה: str) -> bool:
        변수 = "مرحبا بالعالم"
        return True

fn_call = 宇宙Class().계산_함수("✨")
"""
    code_bytes = unicode_code.encode("utf-8")

    for parser, filename in all_parsers:
        nodes, edges = parser.extract(filename, code_bytes)
        assert isinstance(nodes, list)
        assert isinstance(edges, list)


def test_parsers_resilience_on_unterminated_tokens(all_parsers):
    """Verify parsers gracefully handle unclosed quotes, comments, and partial definitions."""
    unterminated_samples = [
        b'def broken_string():\n    msg = "unterminated string\ndef next_func(): pass\n',
        b"/* Unclosed multiline comment that spans to end of file\nclass Foo { int x;\n",
        b"def incomplete_params(a, b, :",
        b"class UnclosedClass {\n    public void method() {\n",
        b"func incompleteGo(a string, b int",
        b"fn incompleteRust<T: Display>(val: T",
    ]

    for parser, filename in all_parsers:
        for s in unterminated_samples:
            nodes, edges = parser.extract(filename, s)
            assert isinstance(nodes, list)
            assert isinstance(edges, list)


def test_cross_platform_path_normalization_fuzz():
    """Verify path normalization properties across arbitrary Windows, POSIX, and mixed slashes."""
    test_cases = [
        ("src\\vibetrace\\db.py", "src/vibetrace/db.py"),
        ("./src/vibetrace/db.py", "src/vibetrace/db.py"),
        (".\\src\\vibetrace\\db.py", "src/vibetrace/db.py"),
        ("./././src/db.py", "src/db.py"),
        ("src/services/../services/trace_service.py", "src/services/../services/trace_service.py"),
        ("C:\\Project\\VibeGraph\\src\\file.py", "C:/Project/VibeGraph/src/file.py"),
    ]

    for raw, expected in test_cases:
        assert _clean_path(raw) == expected

    # Random combinatorial path fuzzing
    random.seed(1337)
    separators = ["/", "\\", "\\\\", "//"]
    segments = ["src", "app", "components", "utils", "index.ts", "test.py", ".", ".."]

    for _ in range(50):
        length = random.randint(2, 6)
        chosen_segs = [random.choice(segments) for _ in range(length)]
        sep = random.choice(separators)
        raw_path = sep.join(chosen_segs)

        cleaned = _clean_path(raw_path)
        # Invariant 1: Cleaned paths must never contain backslashes
        assert "\\" not in cleaned
        # Invariant 2: Cleaned paths must never have a leading redundant './'
        assert not cleaned.startswith("./")
