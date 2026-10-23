"""Standard library module registry across major languages."""

from __future__ import annotations

import sys
from pathlib import Path

NODE_STDLIB: frozenset[str] = frozenset(
    {
        "assert",
        "async_hooks",
        "buffer",
        "child_process",
        "cluster",
        "console",
        "crypto",
        "dgram",
        "dns",
        "events",
        "fs",
        "http",
        "http2",
        "https",
        "module",
        "net",
        "os",
        "path",
        "perf_hooks",
        "process",
        "readline",
        "stream",
        "timers",
        "tls",
        "url",
        "util",
        "v8",
        "vm",
        "worker_threads",
        "zlib",
    }
)

GO_STDLIB: frozenset[str] = frozenset(
    {
        "archive",
        "bufio",
        "bytes",
        "compress",
        "container",
        "context",
        "crypto",
        "database",
        "embed",
        "encoding",
        "errors",
        "flag",
        "fmt",
        "go",
        "hash",
        "html",
        "image",
        "io",
        "log",
        "math",
        "mime",
        "net",
        "os",
        "path",
        "reflect",
        "regexp",
        "runtime",
        "sort",
        "strconv",
        "strings",
        "sync",
        "syscall",
        "testing",
        "text",
        "time",
        "unicode",
        "unsafe",
    }
)

RUST_STDLIB: frozenset[str] = frozenset({"std", "core", "alloc"})

RUBY_STDLIB: frozenset[str] = frozenset(
    {
        "json",
        "csv",
        "yaml",
        "uri",
        "net",
        "fileutils",
        "pathname",
        "logger",
        "optparse",
        "stringio",
        "tempfile",
        "digest",
        "base64",
        "securerandom",
        "set",
        "time",
        "date",
    }
)

FALLBACK_PY_STDLIB: frozenset[str] = frozenset(
    {
        "os",
        "sys",
        "pathlib",
        "json",
        "typing",
        "subprocess",
        "hashlib",
        "re",
        "math",
        "datetime",
        "sqlite3",
        "abc",
    }
)


def get_python_stdlib_names() -> set[str] | frozenset[str]:
    """Retrieve Python standard library module names with fallback."""
    try:
        return sys.stdlib_module_names
    except Exception:
        return FALLBACK_PY_STDLIB


def is_stdlib_import(name: str, file_path: str, py_stdlib: set[str] | frozenset[str]) -> bool:
    """Determine whether an import specifier belongs to the language standard library."""
    if name.startswith("node:"):
        return True
    ext = Path(file_path).suffix.lower()
    if ext == ".py":
        return name.split(".")[0] in py_stdlib
    if ext == ".go":
        return name.split("/")[0] in GO_STDLIB
    if ext in (".js", ".jsx", ".ts", ".tsx", ".mjs", ".cjs"):
        return name.split("/")[0] in NODE_STDLIB
    if ext == ".rs":
        return name.split("::")[0] in RUST_STDLIB
    if ext == ".java":
        return any(name.startswith(pfx) for pfx in ("java.", "javax.", "sun.", "jdk."))
    if ext == ".rb":
        return name.split("/")[0] in RUBY_STDLIB
    root_slash = name.split("/")[0]
    return (
        name.split(".")[0] in py_stdlib
        or root_slash in GO_STDLIB
        or root_slash in NODE_STDLIB
        or name.split("::")[0] in RUST_STDLIB
        or root_slash in RUBY_STDLIB
        or any(name.startswith(pfx) for pfx in ("java.", "javax."))
    )
