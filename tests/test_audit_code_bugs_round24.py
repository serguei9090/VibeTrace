"""Round 24 Audit regression tests.

Verifies:
1. get_git_sha support for both 40-char SHA-1 and 64-char SHA-256 Git hashes.
2. ModuleResolver Go module auto-detection and self-package import resolution.
3. ModuleResolver Cargo crate package name auto-detection and self-crate import resolution.
4. Non-matching external Go modules remain unresolved (None).
"""

from pathlib import Path
from unittest.mock import MagicMock, patch

from vibetrace.indexer.git_utils import get_git_sha
from vibetrace.indexer.resolver import ModuleResolver


def test_get_git_sha_supports_sha1_and_sha256():
    """Verify get_git_sha validates both 40-char SHA-1 and 64-char SHA-256 commit hashes."""
    sha1 = "a" * 40
    sha256 = "b" * 64
    invalid_short = "c" * 39
    invalid_chars = "g" * 40

    with patch("shutil.which", return_value="/usr/bin/git"):
        # Test valid SHA-1
        mock_proc1 = MagicMock(returncode=0, stdout=f"{sha1}\n")
        with patch("subprocess.run", return_value=mock_proc1):
            assert get_git_sha(Path(".")) == sha1

        # Test valid SHA-256
        mock_proc2 = MagicMock(returncode=0, stdout=f"{sha256}\n")
        with patch("subprocess.run", return_value=mock_proc2):
            assert get_git_sha(Path(".")) == sha256

        # Test invalid short SHA
        mock_proc3 = MagicMock(returncode=0, stdout=f"{invalid_short}\n")
        with patch("subprocess.run", return_value=mock_proc3):
            assert get_git_sha(Path(".")) is None

        # Test invalid non-hex characters
        mock_proc4 = MagicMock(returncode=0, stdout=f"{invalid_chars}\n")
        with patch("subprocess.run", return_value=mock_proc4):
            assert get_git_sha(Path(".")) is None


def test_go_module_self_package_resolution(tmp_path: Path):
    """Verify ModuleResolver detects go.mod and resolves internal package imports."""
    proj = tmp_path / "go_proj"
    proj.mkdir()
    (proj / "go.mod").write_text("module github.com/myorg/myapp\n\ngo 1.22\n", encoding="utf-8")

    pkg_auth = proj / "pkg" / "auth"
    pkg_auth.mkdir(parents=True)
    auth_file = pkg_auth / "auth.go"
    auth_file.write_text("package auth\n", encoding="utf-8")

    cmd_server = proj / "cmd" / "server"
    cmd_server.mkdir(parents=True)
    main_file = cmd_server / "main.go"
    main_file.write_text("package main\n", encoding="utf-8")

    resolver = ModuleResolver(proj)
    assert resolver.go_module == "github.com/myorg/myapp"

    # Internal package import should resolve to local auth.go
    res = resolver.resolve("github.com/myorg/myapp/pkg/auth", current_file="cmd/server/main.go")
    assert res == "pkg/auth/auth.go"

    # External package import should NOT match internal module and return None
    ext = resolver.resolve("github.com/gin-gonic/gin", current_file="cmd/server/main.go")
    assert ext is None


def test_cargo_crate_self_package_resolution(tmp_path: Path):
    """Verify ModuleResolver detects Cargo.toml and resolves crate:: and crate_name imports."""
    proj = tmp_path / "rust_proj"
    proj.mkdir()
    cargo_content = """[package]
name = "hyper_service"
version = "0.1.0"
edition = "2021"
"""
    (proj / "Cargo.toml").write_text(cargo_content, encoding="utf-8")

    src = proj / "src"
    src.mkdir()
    (src / "lib.rs").write_text("pub mod config;\n", encoding="utf-8")
    (src / "config.rs").write_text("pub struct Config;\n", encoding="utf-8")

    resolver = ModuleResolver(proj)
    assert resolver.cargo_package == "hyper_service"

    # Test resolving hyper_service::config
    res_pkg = resolver.resolve("hyper_service::config", current_file="src/lib.rs")
    assert res_pkg == "src/config.rs"

    # Test resolving crate/config
    res_crate = resolver.resolve("crate/config", current_file="src/lib.rs")
    assert res_crate == "src/config.rs"
