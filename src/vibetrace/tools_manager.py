"""Polyglot Tooling and Quality Sub-Engine Provisioner.

Provides discovery, diagnostics, and automated on-demand installation of language
quality engines and linters outside of project initialization.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import urllib.request
from dataclasses import dataclass
from pathlib import Path

PHPSTAN_PHAR_URL = "https://github.com/phpstan/phpstan/releases/latest/download/phpstan.phar"


@dataclass
class ToolStatus:
    """Status details for an ecosystem tool / sub-engine."""

    language: str
    engine: str
    is_available: bool
    version_or_path: str | None
    install_hint: str
    auto_installable: bool


class ToolsManager:
    """Manages polyglot quality tool discovery and automated provisioning."""

    @classmethod
    def get_status(cls, project_root: Path | None = None) -> dict[str, ToolStatus]:
        """Returns availability status for all supported language engines."""
        root = (project_root or Path.cwd()).resolve()
        status: dict[str, ToolStatus] = {}

        # 1. Python (Ruff & Pyright)
        which_ruff = shutil.which("ruff")
        which_pyright = shutil.which("pyright")
        py_ok = which_ruff is not None
        py_desc = which_ruff or "Not found"
        if which_pyright:
            py_desc += f", pyright: {which_pyright}"
        status["python"] = ToolStatus(
            language="python",
            engine="Ruff + Pyright",
            is_available=py_ok,
            version_or_path=py_desc if py_ok else None,
            install_hint="uv tool install ruff && uv tool install pyright",
            auto_installable=shutil.which("uv") is not None,
        )

        # 2. JavaScript / TypeScript (Biome & tsc)
        which_biome = shutil.which("biome")
        which_tsc = shutil.which("tsc")
        js_ok = which_biome is not None
        js_desc = which_biome or "Not found"
        if which_tsc:
            js_desc += f", tsc: {which_tsc}"
        status["typescript"] = ToolStatus(
            language="typescript",
            engine="Biome + tsc",
            is_available=js_ok,
            version_or_path=js_desc if js_ok else None,
            install_hint="bun add -d @biomejs/biome typescript (or npm install -D)",
            auto_installable=(shutil.which("bun") is not None or shutil.which("npm") is not None),
        )

        # 3. Go (golangci-lint)
        which_golangci = shutil.which("golangci-lint")
        status["go"] = ToolStatus(
            language="go",
            engine="golangci-lint",
            is_available=which_golangci is not None,
            version_or_path=which_golangci,
            install_hint="go install github.com/golangci/golangci-lint/cmd/golangci-lint@latest",
            auto_installable=shutil.which("go") is not None,
        )

        # 4. Rust (cargo clippy)
        which_cargo = shutil.which("cargo")
        clippy_ok = False
        if which_cargo:
            try:
                res = subprocess.run(
                    ["cargo", "clippy", "--version"],
                    capture_output=True,
                    text=True,
                    check=False,
                    timeout=3.0,
                )
                clippy_ok = res.returncode == 0
            except (OSError, subprocess.SubprocessError):
                clippy_ok = False

        status["rust"] = ToolStatus(
            language="rust",
            engine="cargo clippy",
            is_available=clippy_ok,
            version_or_path="cargo clippy" if clippy_ok else None,
            install_hint="rustup component add clippy",
            auto_installable=shutil.which("rustup") is not None,
        )

        # 5. PHP (PHPStan)
        which_php = shutil.which("php")
        which_phpstan = shutil.which("phpstan")
        user_phar = Path.home() / ".vibetrace" / "bin" / "phpstan.phar"
        local_phar = root / ".vibetrace" / "tools" / "phpstan.phar"
        vendor_bin = root / "vendor" / "bin" / ("phpstan.bat" if os.name == "nt" else "phpstan")

        php_ok = False
        php_desc: str | None = None
        if which_phpstan:
            php_ok = True
            php_desc = which_phpstan
        elif vendor_bin.is_file():
            php_ok = True
            php_desc = str(vendor_bin)
        elif which_php and (user_phar.is_file() or local_phar.is_file()):
            php_ok = True
            php_desc = f"{which_php} {local_phar if local_phar.is_file() else user_phar}"

        status["php"] = ToolStatus(
            language="php",
            engine="PHPStan + Pint",
            is_available=php_ok,
            version_or_path=php_desc,
            install_hint=(
                "vibetrace tools install php  # downloads phpstan.phar or requires composer"
            ),
            auto_installable=True,
        )

        return status

    @classmethod
    def install(cls, language: str, project_root: Path | None = None) -> tuple[bool, str]:
        """Provisions or installs the specified language quality sub-engine."""
        lang = language.lower().strip()
        root = (project_root or Path.cwd()).resolve()

        if lang == "python":
            which_uv = shutil.which("uv")
            if which_uv:
                subprocess.run(["uv", "tool", "install", "ruff"], check=False)
                subprocess.run(["uv", "tool", "install", "pyright"], check=False)
                return True, "Installed Ruff and Pyright via uv tool."
            return False, "uv not found. Install uv first: https://docs.astral.sh/uv/"

        if lang in ("typescript", "javascript", "ts", "js"):
            which_bun = shutil.which("bun")
            if which_bun:
                res = subprocess.run(
                    ["bun", "add", "-d", "@biomejs/biome", "typescript"],
                    cwd=str(root),
                    check=False,
                )
                if res.returncode == 0:
                    return True, "Installed Biome and TypeScript via bun."
            which_npm = shutil.which("npm")
            if which_npm:
                res = subprocess.run(
                    ["npm", "install", "-D", "@biomejs/biome", "typescript"],
                    cwd=str(root),
                    check=False,
                )
                if res.returncode == 0:
                    return True, "Installed Biome and TypeScript via npm."
            return False, "Neither bun nor npm found to install Biome/TypeScript."

        if lang == "go":
            which_go = shutil.which("go")
            if which_go:
                cmd = [
                    which_go,
                    "install",
                    "github.com/golangci/golangci-lint/cmd/golangci-lint@latest",
                ]
                res = subprocess.run(cmd, check=False)
                if res.returncode == 0:
                    return True, "Installed golangci-lint via go install."
            return False, "Go compiler (go) not found in PATH."

        if lang == "rust":
            which_rustup = shutil.which("rustup")
            if which_rustup:
                res = subprocess.run(["rustup", "component", "add", "clippy"], check=False)
                if res.returncode == 0:
                    return True, "Installed cargo clippy component via rustup."
            return False, "rustup not found in PATH."

        if lang == "php":
            # 1. If composer is available in workspace, require phpstan/phpstan
            which_composer = shutil.which("composer")
            if which_composer and (root / "composer.json").is_file():
                res = subprocess.run(
                    [which_composer, "require", "--dev", "phpstan/phpstan"],
                    cwd=str(root),
                    check=False,
                )
                if res.returncode == 0:
                    return True, "Installed PHPStan into vendor/bin/ via Composer."

            # 2. Otherwise download phpstan.phar into ~/.vibetrace/bin
            target_dir = Path.home() / ".vibetrace" / "bin"
            target_dir.mkdir(parents=True, exist_ok=True)
            target_phar = target_dir / "phpstan.phar"

            try:
                urllib.request.urlretrieve(PHPSTAN_PHAR_URL, str(target_phar))
                # Create companion wrapper on Windows (.bat) or POSIX (bash script)
                if os.name == "nt":
                    bat_path = target_dir / "phpstan.bat"
                    bat_path.write_text(f'@echo off\nphp "{target_phar}" %*\n', encoding="utf-8")
                else:
                    sh_path = target_dir / "phpstan"
                    sh_path.write_text(
                        f'#!/usr/bin/env sh\nphp "{target_phar}" "$@"\n', encoding="utf-8"
                    )
                    sh_path.chmod(0o755)

                which_php = shutil.which("php")
                note = "" if which_php else " (Note: Ensure PHP runtime is installed to execute it)"
                return True, f"Downloaded standalone phpstan.phar to {target_phar}{note}."
            except Exception as e:
                return False, f"Failed to download phpstan.phar: {e}"

        langs = "python, typescript, go, rust, php"
        return False, f"Unknown language '{language}'. Supported: {langs}."
