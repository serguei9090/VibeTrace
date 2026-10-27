"""Tests for Monorepo Go/Rust subfolder detection and package-sibling resolution.

Audit Round 48.
"""

from __future__ import annotations

from pathlib import Path

from vibetrace.indexer.parsers.go_parser import GoParser
from vibetrace.indexer.resolver import ModuleResolver


def test_monorepo_go_subfolder_and_package_sibling_resolution(tmp_path: Path):
    """Verify monorepo subfolder go.mod detection and cross-file package symbol resolution."""
    # Setup monorepo structure:
    # workspace/
    #   backend-go/
    #     go.mod (module myorg/backend)
    #     cmd/main.go (calls app.NewService() and app.Helper())
    #     internal/app/
    #       service.go (defines func NewService)
    #       helpers.go (defines func Helper)
    backend = tmp_path / "backend-go"
    (backend / "cmd").mkdir(parents=True)
    (backend / "internal" / "app").mkdir(parents=True)

    (backend / "go.mod").write_text("module myorg/backend\n\ngo 1.22\n", encoding="utf-8")
    (backend / "internal" / "app" / "service.go").write_text(
        "package app\n\nfunc NewService() *Service { return &Service{} }\n",
        encoding="utf-8",
    )
    (backend / "internal" / "app" / "helpers.go").write_text(
        'package app\n\nfunc Helper() string { return "ok" }\n',
        encoding="utf-8",
    )
    main_go = (
        "package main\n\n"
        'import "myorg/backend/internal/app"\n\n'
        "func main() {\n"
        "    s := app.NewService()\n"
        "    _ = app.Helper()\n"
        "}\n"
    )
    (backend / "cmd" / "main.go").write_text(main_go, encoding="utf-8")

    resolver = ModuleResolver(tmp_path)
    assert "myorg/backend" in resolver.go_modules
    assert resolver.go_modules["myorg/backend"] == "backend-go"

    parser = GoParser(resolver)
    _nodes, edges = parser.extract(
        "backend-go/cmd/main.go",
        (backend / "cmd" / "main.go").read_bytes(),
    )

    # Verify calls
    calls = [e for e in edges if e.relation_type == "calls"]
    assert len(calls) == 2

    # Verify NewService resolved to service.go, and Helper resolved to helpers.go
    service_id = parser._get_id("backend-go/internal/app/service.go", "NewService")
    helper_id = parser._get_id("backend-go/internal/app/helpers.go", "Helper")

    assert any(c.to_node_id == service_id for c in calls)
    assert any(c.to_node_id == helper_id for c in calls)


def test_monorepo_rust_subfolder_cargo_toml_detection(tmp_path: Path):
    """Verify monorepo subfolder Cargo.toml package detection."""
    tauri = tmp_path / "src-tauri"
    (tauri / "src").mkdir(parents=True)

    (tauri / "Cargo.toml").write_text(
        '[package]\nname = "my_desktop_app"\nversion = "0.1.0"\n',
        encoding="utf-8",
    )
    (tauri / "src" / "lib.rs").write_text("pub fn init() {}\n", encoding="utf-8")
    (tauri / "src" / "main.rs").write_text(
        "fn main() { my_desktop_app::init(); }\n",
        encoding="utf-8",
    )

    resolver = ModuleResolver(tmp_path)
    assert "my_desktop_app" in resolver.cargo_packages
    assert resolver.cargo_packages["my_desktop_app"] == "src-tauri"

    resolved = resolver.resolve("my_desktop_app/init", "src-tauri/src/main.rs")
    assert resolved in ("src-tauri/src/init.rs", "src-tauri/src/lib.rs", "src-tauri/src/init")


def test_modified_file_line_counts_round48():
    """Ensure resolver.py and base.py adhere strictly to Rule 12 size limits (<= 600 LOC)."""
    root = Path(__file__).parent.parent
    files_to_check = [
        root / "src" / "vibetrace" / "indexer" / "resolver.py",
        root / "src" / "vibetrace" / "indexer" / "parsers" / "base.py",
    ]
    for path in files_to_check:
        lines = len(path.read_text(encoding="utf-8").splitlines())
        assert lines <= 600, f"{path.name} has {lines} lines; must be <= 600"
