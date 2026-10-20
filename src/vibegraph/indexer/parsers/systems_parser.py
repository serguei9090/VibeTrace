"""Multi-language systems parser facade (Go, Rust, C++, Java).

Re-exports language parsers from focused modular subcomponents to adhere to file line ceilings.
"""

from __future__ import annotations

from vibegraph.indexer.parsers.generic_parser import GenericParser
from vibegraph.indexer.parsers.go_parser import GoParser
from vibegraph.indexer.parsers.rust_parser import RustParser

__all__ = ["GenericParser", "GoParser", "RustParser"]
