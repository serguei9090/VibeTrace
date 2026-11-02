"""Tests for RuboCop adapter and Ruby Tree-sitter cognitive complexity."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock, patch

from vibetrace.indexer.parsers import ParserFactory
from vibetrace.quality.adapters.rubocop import RuboCopAdapter
from vibetrace.quality.models import QualityCategory, QualitySeverity
from vibetrace.quality.treesitter_complexity import analyze_treesitter_quality


def test_rubocop_adapter_parse_output(tmp_path: Path):
    sample_json = json.dumps(
        {
            "files": [
                {
                    "path": "app/user.rb",
                    "offenses": [
                        {
                            "severity": "convention",
                            "message": "Prefer single-quoted strings.",
                            "cop_name": "Style/StringLiterals",
                            "location": {"line": 12, "last_line": 12},
                        },
                        {
                            "severity": "warning",
                            "message": "Assignment branch condition size too high.",
                            "cop_name": "Metrics/AbcSize",
                            "location": {"line": 20, "last_line": 40},
                        },
                    ],
                }
            ]
        }
    )
    adapter = RuboCopAdapter()
    findings = adapter.parse_rubocop_output(sample_json, tmp_path)
    assert len(findings) == 2

    abc_finding = next(f for f in findings if f.rule_id == "ruby:S3776")
    assert abc_finding.category == QualityCategory.COMPLEXITY
    assert abc_finding.severity == QualitySeverity.HIGH
    assert abc_finding.rspec_id == "RSPEC-3776"


def test_ruby_treesitter_cognitive_complexity():
    ruby_code = """
class OrderProcessor
  def process_order(a, b, c, d, e, f, g, h)
    score = 0
    if a > 0
      for item in b
        if c && d
          score += 10
        end
      end
    end
    score
  end
end
"""
    parser = ParserFactory.get_parser("order.rb")
    assert parser is not None
    tree = parser.parse(ruby_code.encode("utf-8"))

    comps, findings = analyze_treesitter_quality(
        root_node=tree.root_node,
        file_path="app/order.rb",
        source_lines=ruby_code.splitlines(),
        complexity_threshold=5,
        language_prefix="ruby",
    )

    assert len(comps) == 1
    assert comps[0].name == "process_order"
    assert comps[0].cognitive_complexity > 5

    rule_ids = [f.rule_id for f in findings]
    assert "ruby:S107" in rule_ids, "Should detect 8 parameters"
    assert "ruby:S3776" in rule_ids, "Should detect cognitive complexity"


@patch("subprocess.run")
def test_rubocop_adapter_availability_check(mock_run: MagicMock, tmp_path: Path):
    mock_run.return_value = MagicMock(returncode=0, stdout="1.60.0\n")
    adapter = RuboCopAdapter()
    with patch("shutil.which", return_value="/usr/bin/rubocop"):
        assert adapter.is_available(tmp_path)
