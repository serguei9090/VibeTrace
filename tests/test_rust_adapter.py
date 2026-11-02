"""Unit tests for Rust Cargo Clippy adapter and Sonar RSPEC translation."""

from __future__ import annotations

from pathlib import Path

from vibetrace.quality.adapters.rust_clippy import CargoClippyAdapter
from vibetrace.quality.models import QualityCategory, QualitySeverity


def test_rust_adapter_message_mapping(tmp_path: Path) -> None:
    """Verifies mapping of raw cargo clippy compiler messages to Sonar RSPEC findings."""
    adapter = CargoClippyAdapter()

    # 1. Cognitive complexity message
    cog_msg = {
        "code": {"code": "clippy::cognitive_complexity"},
        "message": "the function has a cognitive complexity of (18/15)",
        "spans": [
            {
                "file_name": "src/algo.rs",
                "line_start": 42,
                "line_end": 75,
                "is_primary": True,
            }
        ],
        "rendered": (
            "warning: the function has a cognitive complexity of (18/15)\n"
            "  --> src/algo.rs:42:1\n"
            "   |\n"
            "42 | fn process() {\n"
        ),
    }

    f1 = adapter._map_compiler_message(cog_msg, tmp_path)
    assert f1 is not None
    assert f1.rspec_id == "RSPEC-3776"
    assert f1.category == QualityCategory.COMPLEXITY
    assert f1.file_path == "src/algo.rs"
    assert f1.start_line == 42

    # 2. Reliability / unwrap_used message
    unwrap_msg = {
        "code": {"code": "clippy::unwrap_used"},
        "message": "used `unwrap()` on an `Option` value",
        "spans": [
            {
                "file_name": "src/main.rs",
                "line_start": 10,
                "line_end": 10,
                "is_primary": True,
            }
        ],
    }

    f2 = adapter._map_compiler_message(unwrap_msg, tmp_path)
    assert f2 is not None
    assert f2.rspec_id == "RSPEC-2970"
    assert f2.category == QualityCategory.RELIABILITY
    assert f2.severity == QualitySeverity.HIGH


def test_rust_adapter_audit_project_filtering(tmp_path: Path, monkeypatch) -> None:
    """Verifies audit_project filters messages by target file list."""
    adapter = CargoClippyAdapter()

    messages = [
        {
            "code": {"code": "clippy::collapsible_if"},
            "message": "this `if` statement can be collapsed",
            "spans": [
                {"file_name": "src/lib.rs", "line_start": 5, "line_end": 8, "is_primary": True}
            ],
        },
        {
            "code": {"code": "clippy::needless_return"},
            "message": "unneeded `return` statement",
            "spans": [
                {"file_name": "src/other.rs", "line_start": 20, "line_end": 20, "is_primary": True}
            ],
        },
    ]

    monkeypatch.setattr(adapter, "_run_clippy", lambda root: messages)

    # Filter to only src/lib.rs
    findings = adapter.audit_project(tmp_path, [tmp_path / "src" / "lib.rs"])
    assert len(findings) == 1
    assert findings[0].file_path == "src/lib.rs"
    assert findings[0].rspec_id == "RSPEC-1066"


def test_rust_adapter_availability_detection(tmp_path: Path) -> None:
    """Verifies is_available returns False when no Cargo.toml exists, and True when created."""
    adapter = CargoClippyAdapter()
    assert adapter.is_available(tmp_path) is False

    cargo_file = tmp_path / "Cargo.toml"
    cargo_file.write_text("[package]\nname = 'test'\nversion = '0.1.0'\n", encoding="utf-8")

    # If cargo is installed on system, availability becomes True
    if adapter._find_runner(tmp_path):
        assert adapter.is_available(tmp_path) is True
