"""Polyglot Tooling and Quality Sub-Engine Provisioner.

Provides discovery, diagnostics, and automated on-demand installation of language
quality engines and linters outside of project initialization.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import urllib.request
import zipfile
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
        is_win = os.name == "nt"
        gl_bin = "golangci-lint.exe" if is_win else "golangci-lint"
        which_golangci = shutil.which(gl_bin)
        if not which_golangci:
            home_go_bin = Path.home() / "go" / "bin" / gl_bin
            if home_go_bin.is_file():
                which_golangci = str(home_go_bin)

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
        bat_name = "phpstan.bat" if os.name == "nt" else "phpstan"
        user_bat = Path.home() / ".vibetrace" / "bin" / bat_name
        local_phar = root / ".vibetrace" / "tools" / "phpstan.phar"
        vendor_bin = root / "vendor" / "bin" / bat_name

        php_ok = False
        php_desc: str | None = None
        if which_phpstan:
            php_ok = True
            php_desc = which_phpstan
        elif vendor_bin.is_file():
            php_ok = True
            php_desc = str(vendor_bin)
        elif user_bat.is_file():
            php_ok = True
            php_desc = str(user_bat)
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

        # 6. Ruby (RuboCop)
        which_rubo = shutil.which("rubocop.bat" if os.name == "nt" else "rubocop")
        which_bundle = shutil.which("bundle")
        if not which_rubo and os.name == "nt":
            for cand in [
                Path("C:/tools/ruby31/bin/rubocop.bat"),
                Path.home() / ".vibetrace" / "bin" / "rubocop.bat",
            ]:
                if cand.is_file():
                    which_rubo = str(cand)
                    break
        ruby_ok = which_rubo is not None
        has_gem = (
            shutil.which("gem") is not None
            or which_bundle is not None
            or os.path.exists("C:/tools/ruby31/bin/gem.cmd")
        )
        status["ruby"] = ToolStatus(
            language="ruby",
            engine="RuboCop",
            is_available=ruby_ok,
            version_or_path=which_rubo,
            install_hint="vibetrace tools install ruby  # executes gem install rubocop",
            auto_installable=has_gem,
        )

        # 7. Shell & Bash (ShellCheck)
        which_sc = shutil.which("shellcheck.exe" if os.name == "nt" else "shellcheck")
        if not which_sc:
            sc_name = "shellcheck.exe" if os.name == "nt" else "shellcheck"
            sc_home = Path.home() / ".vibetrace" / "bin" / sc_name
            if sc_home.is_file():
                which_sc = str(sc_home)
        status["shell"] = ToolStatus(
            language="shell",
            engine="ShellCheck",
            is_available=which_sc is not None,
            version_or_path=which_sc,
            install_hint=(
                "winget install koalaman.shellcheck (Win) or apt install shellcheck (Linux)"
            ),
            auto_installable=True,
        )

        # 8. SQL (SQLFluff)
        is_win = os.name == "nt"
        sql_bin_name = "sqlfluff.exe" if is_win else "sqlfluff"
        which_sql = shutil.which(sql_bin_name)
        if not which_sql:
            venv_bin = root / ".venv" / ("Scripts" if is_win else "bin") / sql_bin_name
            if venv_bin.is_file():
                which_sql = str(venv_bin)

        status["sql"] = ToolStatus(
            language="sql",
            engine="SQLFluff",
            is_available=which_sql is not None,
            version_or_path=which_sql,
            install_hint="vibetrace tools install sql  # installs sqlfluff via uv or pip",
            auto_installable=shutil.which("uv") is not None or shutil.which("pip") is not None,
        )

        # 9. Semgrep (External SAST Adapter)
        semgrep_bin = "semgrep.exe" if is_win else "semgrep"
        which_semgrep = shutil.which(semgrep_bin) or shutil.which("semgrep")
        if not which_semgrep:
            venv_semgrep = root / ".venv" / ("Scripts" if is_win else "bin") / semgrep_bin
            if venv_semgrep.is_file():
                which_semgrep = str(venv_semgrep)
        status["semgrep"] = ToolStatus(
            language="semgrep",
            engine="Semgrep SAST CLI",
            is_available=which_semgrep is not None,
            version_or_path=which_semgrep,
            install_hint="vibetrace tools install semgrep  # installs semgrep via uv tool or pip",
            auto_installable=shutil.which("uv") is not None or shutil.which("pip") is not None,
        )

        # 10. Trivy (Container & OS CVE Adapter)
        trivy_bin = "trivy.exe" if is_win else "trivy"
        which_trivy = shutil.which(trivy_bin) or shutil.which("trivy")
        status["trivy"] = ToolStatus(
            language="trivy",
            engine="Aqua Trivy (Container & OS CVEs)",
            is_available=which_trivy is not None,
            version_or_path=which_trivy,
            install_hint="vibetrace tools install trivy  # installs trivy via winget or brew",
            auto_installable=(
                shutil.which("winget") is not None or shutil.which("brew") is not None
            ),
        )

        # 11. Hybrid Rust Native Core (`vibetrace._rust_core`)
        from vibetrace.indexer.rust_bridge import get_native_version, is_native_available

        native_ok = is_native_available()
        native_ver = get_native_version()
        status["rust_core"] = ToolStatus(
            language="rust_core",
            engine="PyO3 Hybrid Rust Engine (_rust_core)",
            is_available=native_ok,
            version_or_path=f"v{native_ver} (active)" if native_ver else None,
            install_hint="cargo build --release -p vibetrace-core --features extension-module",
            auto_installable=which_cargo is not None,
        )

        # 12. SonarLint Headless Java Engine (Tier-2 Optional Sidecar)
        from vibetrace.quality.adapters.sonar_lint import resolve_sonar_runtime

        sonar_rt = resolve_sonar_runtime()
        sonar_ok = sonar_rt is not None
        sonar_desc = (
            f"{sonar_rt.ls_jar} ({len(sonar_rt.analyzer_jars)} analyzers, {sonar_rt.source_kind})"
            if sonar_rt
            else None
        )
        status["sonar"] = ToolStatus(
            language="sonar",
            engine="SonarLint Headless LSP (Java 21 + LGPL v3 Analyzers)",
            is_available=sonar_ok,
            version_or_path=sonar_desc,
            install_hint="vibetrace tools install sonar  # provisions sonarlint-ls.jar + JRE",
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
                # Validate downloaded phar is not empty or corrupted HTML error page
                if not target_phar.is_file() or target_phar.stat().st_size < 100_000:
                    target_phar.unlink(missing_ok=True)
                    msg = "Downloaded phpstan.phar failed integrity check (truncated/invalid)."
                    return False, msg
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

        if lang == "ruby":
            which_gem = shutil.which("gem")
            if not which_gem and os.name == "nt" and os.path.exists("C:/tools/ruby31/bin/gem.cmd"):
                which_gem = "C:/tools/ruby31/bin/gem.cmd"
            if which_gem:
                if os.name == "nt":
                    subprocess.run(
                        [which_gem, "install", "rubocop-ast", "-v", "1.30.0", "--no-document"],
                        check=False,
                    )
                    res = subprocess.run(
                        [which_gem, "install", "rubocop", "-v", "1.59.0", "--no-document"],
                        check=False,
                    )
                else:
                    res = subprocess.run([which_gem, "install", "rubocop"], check=False)
                if res.returncode == 0:
                    return True, "Installed RuboCop via gem install."
            which_bundle = shutil.which("bundle")
            if which_bundle and (root / "Gemfile").is_file():
                res = subprocess.run(
                    [which_bundle, "add", "rubocop", "--group", "development"],
                    cwd=str(root),
                    check=False,
                )
                if res.returncode == 0:
                    return True, "Added RuboCop to Gemfile via bundle add."
            return False, "Ruby package manager ('gem' or 'bundle') not found in PATH."

        if lang in ("shell", "bash", "sh"):
            which_winget = shutil.which("winget")
            if which_winget:
                res = subprocess.run(
                    ["winget", "install", "--id", "koalaman.shellcheck", "-e", "--silent"],
                    check=False,
                )
                if res.returncode == 0:
                    return True, "Installed ShellCheck via Windows winget."
            which_brew = shutil.which("brew")
            if which_brew:
                res = subprocess.run(["brew", "install", "shellcheck"], check=False)
                if res.returncode == 0:
                    return True, "Installed ShellCheck via Homebrew."
            # Fallback: direct portable zip download on Windows
            if os.name == "nt":
                try:
                    bin_dir = Path.home() / ".vibetrace" / "bin"
                    bin_dir.mkdir(parents=True, exist_ok=True)
                    zip_path = bin_dir / "shellcheck_temp.zip"
                    url = (
                        "https://github.com/koalaman/shellcheck/releases/download/"
                        "v0.10.0/shellcheck-v0.10.0.zip"
                    )
                    urllib.request.urlretrieve(url, zip_path)
                    with zipfile.ZipFile(zip_path, "r") as zf:
                        zf.extractall(bin_dir)
                    if zip_path.is_file():
                        zip_path.unlink()
                    return True, f"Downloaded and extracted standalone ShellCheck to {bin_dir}."
                except Exception as e:
                    return False, f"Failed to download ShellCheck: {e}"
            return False, "Package manager not found. Please install shellcheck manually."

        if lang in ("sql", "sqlfluff"):
            which_uv = shutil.which("uv")
            if which_uv:
                res = subprocess.run(
                    [which_uv, "pip", "install", "sqlfluff"], cwd=str(root), check=False
                )
                if res.returncode == 0:
                    return True, "Installed SQLFluff via uv pip."
                res2 = subprocess.run([which_uv, "tool", "install", "sqlfluff"], check=False)
                if res2.returncode == 0:
                    return True, "Installed SQLFluff via uv tool."
            which_pip = shutil.which("pip")
            if which_pip:
                res = subprocess.run([which_pip, "install", "sqlfluff"], check=False)
                if res.returncode == 0:
                    return True, "Installed SQLFluff via pip."
            return False, "Python package manager (uv/pip) not found to install sqlfluff."

        if lang == "semgrep":
            which_uv = shutil.which("uv")
            if which_uv:
                res = subprocess.run([which_uv, "tool", "install", "semgrep"], check=False)
                if res.returncode == 0:
                    return True, "Installed Semgrep CLI via uv tool."
                res2 = subprocess.run(
                    [which_uv, "pip", "install", "semgrep"], cwd=str(root), check=False
                )
                if res2.returncode == 0:
                    return True, "Installed Semgrep CLI via uv pip."
            which_pipx = shutil.which("pipx")
            if which_pipx:
                res = subprocess.run([which_pipx, "install", "semgrep"], check=False)
                if res.returncode == 0:
                    return True, "Installed Semgrep CLI via pipx."
            which_pip = shutil.which("pip")
            if which_pip:
                res = subprocess.run([which_pip, "install", "semgrep"], check=False)
                if res.returncode == 0:
                    return True, "Installed Semgrep CLI via pip."
            which_brew = shutil.which("brew")
            if which_brew:
                res = subprocess.run(["brew", "install", "semgrep"], check=False)
                if res.returncode == 0:
                    return True, "Installed Semgrep CLI via Homebrew."
            return False, "Package manager (uv/pipx/pip/brew) not found to install semgrep."

        if lang == "trivy":
            which_winget = shutil.which("winget")
            if which_winget:
                res = subprocess.run(
                    ["winget", "install", "--id", "AquaSecurity.Trivy", "-e", "--silent"],
                    check=False,
                )
                if res.returncode == 0:
                    return True, "Installed Aqua Trivy via Windows winget."
            which_brew = shutil.which("brew")
            if which_brew:
                res = subprocess.run(["brew", "install", "trivy"], check=False)
                if res.returncode == 0:
                    return True, "Installed Aqua Trivy via Homebrew."
            return False, "Package manager (winget/brew) not found to install Trivy."

        if lang in ("lsp", "install-lsp", "lsp-server"):
            # Auto-detect project languages and install best matching LSP
            has_py = (
                any(root.glob("*.py"))
                or any(root.glob("*/*.py"))
                or (root / "pyproject.toml").exists()
            )
            has_ts = (
                any(root.glob("*.ts"))
                or any(root.glob("*.tsx"))
                or any(root.glob("*/*.ts"))
                or (root / "package.json").exists()
            )
            has_go = any(root.glob("*.go")) or (root / "go.mod").exists()
            has_rs = any(root.glob("*.rs")) or (root / "Cargo.toml").exists()

            results: list[str] = []
            which_uv = shutil.which("uv")
            which_npm = shutil.which("npm")
            which_bun = shutil.which("bun")

            # Python LSP (basedpyright or pyright)
            if has_py or (not has_ts and not has_go and not has_rs):
                if which_uv:
                    res = subprocess.run([which_uv, "tool", "install", "basedpyright"], check=False)
                    if res.returncode == 0:
                        results.append("basedpyright (Python)")
                    else:
                        res2 = subprocess.run([which_uv, "tool", "install", "pyright"], check=False)
                        if res2.returncode == 0:
                            results.append("pyright (Python)")

            # TypeScript LSP (typescript-language-server)
            if has_ts:
                pm = which_bun or which_npm
                if pm:
                    cmd_install = (
                        [pm, "add", "-g", "typescript-language-server", "typescript"]
                        if which_bun
                        else [pm, "install", "-g", "typescript-language-server", "typescript"]
                    )
                    res = subprocess.run(cmd_install, check=False)
                    if res.returncode == 0:
                        results.append("typescript-language-server (TypeScript/JS)")

            if results:
                return True, f"Successfully installed LSP servers: {', '.join(results)}."
            hint = "Run 'uv tool install pyright' or 'npm i -g typescript-language-server'."
            return False, f"Could not automatically install LSP. {hint}"

        if lang in ("sonar", "sonarlint", "sonarqube"):
            return cls._install_sonar_engine()

        langs = "python, typescript, go, rust, php, ruby, shell, sql, semgrep, trivy, sonar, lsp"
        return False, f"Unknown language/tool '{language}'. Supported: {langs}."

    @classmethod
    def _install_sonar_engine(cls) -> tuple[bool, str]:
        """Provisions the SonarLint Headless Java Engine into `~/.vibetrace/tools/sonar/`."""
        import platform

        from vibetrace.quality.adapters.sonar_lint import resolve_sonar_runtime

        target_root = Path.home() / ".vibetrace" / "tools" / "sonar"
        target_root.mkdir(parents=True, exist_ok=True)
        work_dir = target_root / "work"
        work_dir.mkdir(parents=True, exist_ok=True)

        # 1. If already installed inside ~/.vibetrace/tools/sonar or local IDE, sync/verify
        existing = resolve_sonar_runtime()
        if existing is not None and existing.source_kind == "vibetrace":
            return (
                True,
                f"SonarLint engine already provisioned at {existing.root_dir} "
                f"({len(existing.analyzer_jars)} analyzers).",
            )

        if existing is not None and existing.source_kind != "vibetrace":
            try:
                for sub in ("server", "analyzers", "jre"):
                    src_sub = existing.root_dir / sub
                    dst_sub = target_root / sub
                    if src_sub.is_dir():
                        if dst_sub.exists():
                            shutil.rmtree(dst_sub, ignore_errors=True)
                        shutil.copytree(src_sub, dst_sub)
                verified = resolve_sonar_runtime()
                if verified is not None:
                    return (
                        True,
                        f"Provisioned standalone SonarLint engine into {target_root} "
                        f"(from local {existing.source_kind} extension, "
                        f"{len(verified.analyzer_jars)} analyzers).",
                    )
            except OSError:
                return (
                    True,
                    f"Using local {existing.source_kind} SonarLint engine at {existing.root_dir} "
                    f"with isolated workdir {work_dir}.",
                )

        # 2. Download official OpenVSX / GitHub VSIX bundle for current platform
        sys_plat = (
            "win32"
            if os.name == "nt"
            else ("darwin" if platform.system().lower() == "darwin" else "linux")
        )
        arch = "arm64" if "arm" in platform.machine().lower() else "x64"
        target_plat = f"{sys_plat}-{arch}"
        vsix_url = (
            "https://open-vsx.org/api/SonarSource/sonarlint-vscode/"
            f"{target_plat}/latest/file/SonarSource.sonarlint-vscode-{target_plat}.vsix"
        )
        vsix_tmp = target_root / "sonarlint_bundle.vsix"
        ext_dir = target_root / "extension"
        resolved_target = target_root.resolve()
        try:
            urllib.request.urlretrieve(vsix_url, str(vsix_tmp))
            if not vsix_tmp.is_file() or vsix_tmp.stat().st_size < 1_000_000:
                vsix_tmp.unlink(missing_ok=True)
                return False, "Downloaded SonarLint VSIX bundle failed size integrity check."

            with zipfile.ZipFile(vsix_tmp, "r") as zf:
                for member in zf.namelist():
                    if not any(
                        member.startswith(p)
                        for p in ("extension/server/", "extension/analyzers/", "extension/jre/")
                    ):
                        continue
                    member_dest = (target_root / member).resolve()
                    if not member_dest.is_relative_to(resolved_target):
                        continue
                    zf.extract(member, target_root)

            if ext_dir.is_dir():
                for sub in ("server", "analyzers", "jre"):
                    src_s = ext_dir / sub
                    dst_s = target_root / sub
                    if src_s.is_dir():
                        if dst_s.exists():
                            shutil.rmtree(dst_s, ignore_errors=True)
                        shutil.move(str(src_s), str(dst_s))
                shutil.rmtree(ext_dir, ignore_errors=True)

            vsix_tmp.unlink(missing_ok=True)
            verified = resolve_sonar_runtime()
            if verified is not None:
                if os.name != "nt" and verified.java_bin.is_file():
                    verified.java_bin.chmod(0o755)
                return (
                    True,
                    f"Downloaded and provisioned SonarLint engine at {target_root} "
                    f"({len(verified.analyzer_jars)} analyzers).",
                )
            return False, "Extracted SonarLint bundle but runtime verification failed."
        except Exception as exc:
            vsix_tmp.unlink(missing_ok=True)
            if ext_dir.exists():
                shutil.rmtree(ext_dir, ignore_errors=True)
            return False, f"Failed to download SonarLint bundle: {exc}"
