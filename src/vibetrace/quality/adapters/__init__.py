"""Native linter and type-checker sub-engine adapters for VibeTrace."""

from __future__ import annotations

from vibetrace.quality.adapters.base import LinterSubEngine
from vibetrace.quality.adapters.biome import BiomeAdapter
from vibetrace.quality.adapters.golang_lint import GolangLintAdapter
from vibetrace.quality.adapters.php_stan import PhpStanAdapter
from vibetrace.quality.adapters.python_pyright import PyrightAdapter
from vibetrace.quality.adapters.python_ruff import RuffAdapter
from vibetrace.quality.adapters.rubocop import RuboCopAdapter
from vibetrace.quality.adapters.rust_clippy import CargoClippyAdapter
from vibetrace.quality.adapters.shellcheck import ShellCheckAdapter
from vibetrace.quality.adapters.typescript_tsc import TypeScriptAdapter

__all__ = [
    "BiomeAdapter",
    "CargoClippyAdapter",
    "GolangLintAdapter",
    "LinterSubEngine",
    "PhpStanAdapter",
    "PyrightAdapter",
    "RuboCopAdapter",
    "RuffAdapter",
    "ShellCheckAdapter",
    "TypeScriptAdapter",
]
