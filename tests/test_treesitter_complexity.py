"""Tests for universal Tree-sitter cognitive complexity and AST smell analysis."""

from __future__ import annotations

from vibetrace.indexer.parsers import ParserFactory
from vibetrace.quality.models import QualityCategory, QualitySeverity
from vibetrace.quality.treesitter_complexity import analyze_treesitter_quality


def test_treesitter_cognitive_complexity_and_smells():
    php_code = """<?php
class ReportGenerator {
    // S107: 8 parameters (> 7)
    public function generateReport($a, $b, $c, $d, $e, $f, $g, $h) {
        $score = 0;
        // Nesting +1, condition +1
        if ($a > 0) {
            for ($i = 0; $i < $b; $i++) { // nesting 2 (+2)
                if ($c && $d) {           // nesting 3 (+3 + 1)
                    $score += 10;
                }
            }
        }
        return $score;
    }
}
"""
    parser = ParserFactory.get_parser("sample.php")
    assert parser is not None
    tree = parser.parse(php_code.encode("utf-8"))

    comps, findings = analyze_treesitter_quality(
        root_node=tree.root_node,
        file_path="src/ReportGenerator.php",
        source_lines=php_code.splitlines(),
        complexity_threshold=5,
        language_prefix="php",
    )

    assert len(comps) == 1
    assert comps[0].name == "generateReport"
    assert comps[0].cognitive_complexity > 5

    rule_ids = [f.rule_id for f in findings]
    assert "php:S107" in rule_ids, "Should detect parameter bloat S107"
    assert "php:S3776" in rule_ids, "Should detect high cognitive complexity S3776"

    s107 = next(f for f in findings if f.rule_id == "php:S107")
    assert "8 parameters" in s107.message
    assert s107.category == QualityCategory.MAINTAINABILITY

    s3776 = next(f for f in findings if f.rule_id == "php:S3776")
    assert s3776.severity == QualitySeverity.HIGH
    assert s3776.category == QualityCategory.COMPLEXITY


def test_treesitter_nested_ternary_and_mergeable_if():
    php_code = """<?php
class Validator {
    public function validate($x, $y) {
        // S3358: nested ternary
        $val = $x ? ($y ? 1 : 2) : 3;

        // S1066: mergeable if
        if ($x > 0) {
            if ($y > 0) {
                echo "merged";
            }
        }

        // S3626: redundant return
        return;
    }
}
"""
    parser = ParserFactory.get_parser("sample.php")
    assert parser is not None
    tree = parser.parse(php_code.encode("utf-8"))

    _comps, findings = analyze_treesitter_quality(
        root_node=tree.root_node,
        file_path="src/Validator.php",
        source_lines=php_code.splitlines(),
        complexity_threshold=15,
        language_prefix="php",
    )

    rule_ids = [f.rule_id for f in findings]
    assert "php:S3358" in rule_ids, "Should detect nested ternary S3358"
    assert "php:S1066" in rule_ids, "Should detect mergeable if S1066"
    assert "php:S3626" in rule_ids, "Should detect redundant return S3626"


def test_treesitter_clean_code():
    php_code = """<?php
class Calculator {
    public function add(int $a, int $b): int {
        return $a + $b;
    }
}
"""
    parser = ParserFactory.get_parser("sample.php")
    assert parser is not None
    tree = parser.parse(php_code.encode("utf-8"))

    comps, findings = analyze_treesitter_quality(
        root_node=tree.root_node,
        file_path="src/Calculator.php",
        source_lines=php_code.splitlines(),
        complexity_threshold=15,
        language_prefix="php",
    )
    assert len(findings) == 0, "Clean code should produce no findings"
    assert len(comps) == 1
    assert comps[0].cognitive_complexity == 0
