"""Unit tests for PHP PHPStan / Pint adapter and Sonar RSPEC translation."""

from __future__ import annotations

import json
from pathlib import Path

from vibetrace.quality.adapters.php_stan import PhpStanAdapter
from vibetrace.quality.models import QualityCategory, QualitySeverity


def test_php_adapter_message_mapping(tmp_path: Path) -> None:
    """Verifies mapping of PHPStan JSON diagnostics to canonical Sonar RSPEC findings."""
    adapter = PhpStanAdapter()

    # 1. Cognitive complexity finding
    cog_msg = {
        "message": "Function process() has a cognitive complexity of 18 (max 15).",
        "line": 45,
        "identifier": "cognitiveComplexity.tooHigh",
    }
    f1 = adapter._map_message_to_finding(cog_msg, "src/Controller.php", tmp_path)
    assert f1 is not None
    assert f1.rspec_id == "RSPEC-3776"
    assert f1.category == QualityCategory.COMPLEXITY
    assert f1.severity == QualitySeverity.HIGH
    assert f1.file_path == "src/Controller.php"
    assert f1.start_line == 45

    # 2. Method not found / undefined symbol
    undef_msg = {
        "message": "Call to an undefined method App\\User::calculateTotal().",
        "line": 88,
        "identifier": "method.notFound",
    }
    f2 = adapter._map_message_to_finding(undef_msg, "src/Order.php", tmp_path)
    assert f2 is not None
    assert f2.rspec_id == "RSPEC-3785"
    assert f2.category == QualityCategory.RELIABILITY
    assert f2.severity == QualitySeverity.HIGH

    # 3. Deprecated method usage
    dep_msg = {
        "message": "Call to deprecated method legacyAuth() of class App\\Auth.",
        "line": 12,
        "identifier": "method.deprecated",
    }
    f3 = adapter._map_message_to_finding(dep_msg, "src/Login.php", tmp_path)
    assert f3 is not None
    assert f3.rspec_id == "RSPEC-1874"
    assert f3.category == QualityCategory.MAINTAINABILITY
    assert f3.severity == QualitySeverity.MEDIUM

    # 4. Unused variable
    unused_msg = {
        "message": "Variable $temp is unused.",
        "line": 20,
        "identifier": "variable.unused",
    }
    f4 = adapter._map_message_to_finding(unused_msg, "src/Helper.php", tmp_path)
    assert f4 is not None
    assert f4.rspec_id == "RSPEC-1481"
    assert f4.category == QualityCategory.MAINTAINABILITY
    assert f4.severity == QualitySeverity.LOW


def test_php_adapter_parse_phpstan_output(tmp_path: Path) -> None:
    """Verifies parsing of standard PHPStan JSON error format with multiple files."""
    adapter = PhpStanAdapter()

    sample_output = json.dumps(
        {
            "totals": {"errors": 0, "file_errors": 2},
            "files": {
                "src/Service.php": {
                    "errors": 1,
                    "messages": [
                        {
                            "message": "Method Service::run() has no return type specified.",
                            "line": 10,
                            "identifier": "return.missing",
                        }
                    ],
                },
                "src/Repository.php": {
                    "errors": 1,
                    "messages": [
                        {
                            "message": "Dead catch block, exception never thrown.",
                            "line": 50,
                            "identifier": "deadCode.unreachable",
                        }
                    ],
                },
            },
            "errors": [],
        }
    )

    findings = adapter._parse_phpstan_output(sample_output, tmp_path)
    assert len(findings) == 2
    rule_ids = {f.rspec_id for f in findings}
    assert "RSPEC-3626" in rule_ids
    assert "RSPEC-1854" in rule_ids


def test_php_adapter_audit_project_filtering(tmp_path: Path, monkeypatch) -> None:
    """Verifies audit_project filters findings to only target files in file_list."""
    adapter = PhpStanAdapter()

    sample_json = json.dumps(
        {
            "files": {
                "src/Target.php": {
                    "messages": [
                        {
                            "message": "Unused $var",
                            "line": 5,
                            "identifier": "variable.unused",
                        }
                    ]
                },
                "src/Ignored.php": {
                    "messages": [
                        {
                            "message": "Unused $other",
                            "line": 10,
                            "identifier": "variable.unused",
                        }
                    ]
                },
            }
        }
    )

    monkeypatch.setattr(adapter, "_run_phpstan", lambda root, args: sample_json)

    # Filter to only Target.php
    findings = adapter.audit_project(tmp_path, [tmp_path / "src" / "Target.php"])
    assert len(findings) == 1
    assert findings[0].file_path == "src/Target.php"


def test_php_adapter_availability_detection(tmp_path: Path) -> None:
    """Verifies is_available checks runner and composer.json or .php files."""
    adapter = PhpStanAdapter()
    assert adapter.is_available(tmp_path) is False

    comp = tmp_path / "composer.json"
    comp.write_text('{"name": "vendor/project"}\n', encoding="utf-8")

    if adapter._find_runner(tmp_path):
        assert adapter.is_available(tmp_path) is True
