"""Bi-directional Codebase Symbol <-> Documentation Linking Engine.

Maintains live connections between AST symbols and Markdown specifications,
enforces context window token budgets on code slices, and audits documentation
for broken symbol references (preventing architectural documentation drift).
"""

from __future__ import annotations

import re
import sqlite3
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field

from vibetrace.docs.chunker import chunk_markdown
from vibetrace.indexer.db import IndexerDB

# Filter out common programming keywords, schema entities, parameters, and primitives
_DOC_SYMBOL_IGNORE = frozenset(
    {
        # Primitives & keywords
        "true",
        "false",
        "none",
        "null",
        "undefined",
        "string",
        "str",
        "int",
        "float",
        "bool",
        "boolean",
        "dict",
        "list",
        "tuple",
        "set",
        "def",
        "class",
        "fn",
        "function",
        "return",
        "import",
        "from",
        "self",
        "cls",
        "this",
        "let",
        "const",
        "var",
        "require",
        "super",
        "yield",
        "nonlocal",
        "global",
        "break",
        "continue",
        "get",
        "post",
        "put",
        "delete",
        "patch",
        "head",
        "options",
        "any",
        "all",
        "ok",
        "err",
        "error",
        "id",
        "key",
        "value",
        "val",
        "data",
        "args",
        "kwargs",
        "type",
        "name",
        "item",
        # Common CLI flags, MCP tool arguments, config fields
        "symbol_name",
        "package_name",
        "file_path",
        "scope_path",
        "max_depth",
        "depth",
        "query",
        "limit",
        "budget",
        "budget_tokens",
        "action",
        "force",
        "staged",
        "apply",
        "sync",
        "embed",
        "reason",
        "rationale",
        "finding_id",
        "min_coverage",
        "enforce_coverage",
        "include_code",
        "include_callees",
        "include_divergent",
        "response_format",
        "from_symbol",
        "to_symbol",
        "caller_name",
        "callee_a",
        "callee_b",
        "signature_pattern",
        "tag",
        "scope",
        "suppress_unpatched",
        "check_dependencies",
        "analyze_taint",
        "repo",
        "catalog",
        "description",
        "helper_name",
        "start_line",
        "end_line",
        "path",
        "lines",
        "write",
        "strict",
        "command",
        "host",
        "port",
        "verbose",
        # Schema entity names & DB columns
        "nodes",
        "edges",
        "indexed_files",
        "symbol_doc_links",
        "call_line",
        "qualified_name",
        "content_hash",
        "updated_at",
        "from_node_id",
        "to_node_id",
        "relation_type",
        "node_id",
        "node_embeddings",
        "node_fts",
        "centrality",
        "decorators",
        "visibility",
        "docstring",
        "signature",
        "parent_id",
        # Advisory and documentation tokens
        "cve",
        "ghsa",
        "pysec",
        "moderate",
        "high",
        "low",
        "critical",
        "added",
        "changed",
        "fixed",
        "security",
        "deprecated",
        "removed",
        "unapproved",
        "approved",
        "modified",
        "note",
        "tip",
        "warning",
        "caution",
        "important",
        "dockerfile",
        "makefile",
        "license",
        "gemfile",
        "procfile",
        "vagrantfile",
        "changelog",
        "fastapi",
        "express",
        "next.js",
        "classname.method_name",
        "raw_sql",
        "antigravity.json",
        "claude_desktop_config.json",
        "click",
        "httpx",
        "certifi",
        "dependencies",
        "impact",
        "cwe",
        "extra",
        "dompurify",
        "encodeuricomponent",
        "execsync",
        "usecallback",
        "usememo",
        "requirements.txt",
        "splitapprovalrequest",
        "baseparser",
        "like",
        "regexp",
        "stdlib",
        "thirdparty",
        "internal",
        "last_indexed_git_sha",
        "saved_traces",
        "untainted_local",
        "untainted_call_arg",
        "pip",
        "bun",
        "cargo",
        "feat",
        "fix",
        "perf",
        "chore",
        "pgvector",
        "flashrank",
        "tiktoken",
        "stdio",
        "sse",
    }
)

_KNOWN_FILE_EXTENSIONS = (
    ".py",
    ".ts",
    ".tsx",
    ".js",
    ".jsx",
    ".rs",
    ".go",
    ".lock",
    ".json",
    ".toml",
    ".yaml",
    ".yml",
    ".xml",
    ".info",
    ".md",
    ".txt",
    ".html",
    ".css",
    ".scss",
    ".svg",
    ".png",
    ".jpg",
    ".jpeg",
    ".gif",
    ".ico",
    ".sh",
    ".bash",
    ".ps1",
    ".bat",
    ".cmd",
    ".sql",
    ".db",
    ".sqlite",
    ".db3",
    ".wasm",
    ".env",
    ".cfg",
    ".ini",
    ".conf",
    ".proto",
    ".graphql",
    ".gql",
    ".csv",
    ".tsv",
)

_IGNORED_ROOT_MODULES = frozenset(
    {
        "os",
        "sys",
        "re",
        "json",
        "subprocess",
        "pathlib",
        "pickle",
        "yaml",
        "urllib",
        "sqlite3",
        "typing",
        "dataclasses",
        "time",
        "datetime",
        "math",
        "asyncio",
        "logging",
        "shutil",
        "tempfile",
        "hashlib",
        "unittest",
        "pytest",
        "fastapi",
        "pydantic",
        "lancedb",
        "mcp",
        "console",
        "document",
        "window",
        "process",
        "child_process",
        "fs",
        "path",
        "http",
        "react",
        "next",
        "express",
        "html",
        "cursor",
        "elementtree",
        "sql",
        "git",
        "django",
        "flask",
        "sqlalchemy",
        "test",
    }
)

_BACKTICK_SYMBOL_RE = re.compile(r"`([A-Za-z_][A-Za-z0-9_.]*(?:\(\))?)`")
_IGNORED_DIRS = {
    ".git",
    ".venv",
    "node_modules",
    "dist",
    "build",
    "scratch",
    "archive",
    "__pycache__",
    ".ruff_cache",
    ".pytest_cache",
    ".gemini",
    ".vscode",
    ".idea",
    "target",
    "tmp",
    "temp",
    "site",
    "htmlcov",
    "extensions",
}

_IGNORED_ROOT_FILES = {
    "changelog.md",
    "license.md",
    "contributing.md",
    "code_of_conduct.md",
    "forlater.md",
    "project.md",
    "report.md",
    "report_ai_test.md",
    "ui.md",
}


class LinkedDocReference(BaseModel):
    """Governing architectural specification attached to an AST symbol."""

    doc_path: str
    heading: str
    heading_path: str
    start_line: int
    end_line: int
    content: str
    token_count: int = 0


class DocLinkAuditFinding(BaseModel):
    """Represents a broken or stale symbol reference found in documentation."""

    doc_path: str
    line_number: int
    raw_symbol: str
    issue: str
    suggested_symbol: str | None = None


class DocLinkAuditReport(BaseModel):
    """Report detailing documentation symbol reference health and broken links."""

    total_doc_files: int = 0
    total_symbols_referenced: int = 0
    valid_links_count: int = 0
    broken_links_count: int = 0
    passed: bool = True
    findings: list[DocLinkAuditFinding] = Field(default_factory=list)


def estimate_tokens(text: str) -> int:
    """Rough token count estimation (~4 characters per token)."""
    return max(1, len(text) // 4)


class SymbolDocLinker:
    """Synchronizes, retrieves, and audits bi-directional code-to-doc relationships."""

    @classmethod
    def extract_backticked_symbols(cls, text: str) -> list[str]:
        """Extracts valid code symbol candidate identifiers from Markdown text."""
        candidates: list[str] = []
        for match in _BACKTICK_SYMBOL_RE.finditer(text):
            raw = match.group(1).removesuffix("()").strip()
            # Filter out single letters, numbers, or standard language keywords
            if len(raw) <= 2 or raw.lower() in _DOC_SYMBOL_IGNORE:
                continue
            # Filter out file extensions, ranges, or paths
            if raw.lower().endswith(_KNOWN_FILE_EXTENSIONS):
                continue
            if ".." in raw or raw.startswith(".") or raw.endswith(".") or "/" in raw or "\\" in raw:
                continue
            # Filter out all-uppercase words (HTTP verbs, SQL clauses, acronyms)
            if raw.isupper():
                continue
            # Filter out external stdlib or framework root modules
            root_mod = raw.split(".")[0] if "." in raw else raw
            if root_mod.lower() in _IGNORED_ROOT_MODULES:
                continue
            # Must look like an identifier (contains uppercase, underscore, or dot)
            if any(c.isupper() for c in raw) or "_" in raw or "." in raw:
                candidates.append(raw)
        return candidates

    @classmethod
    def find_markdown_files(cls, project_root: Path) -> list[Path]:
        """Collects all relevant Markdown files in the project."""
        md_files: list[Path] = []
        for p in project_root.rglob("*.md"):
            # Exclude ignored directories
            if any(part in _IGNORED_DIRS for part in p.parts):
                continue
            try:
                rel = p.relative_to(project_root)
            except ValueError:
                rel = p
            # Root-level markdown files must be primary docs, not scratch or changelogs
            if len(rel.parts) == 1 and rel.name.lower() in _IGNORED_ROOT_FILES:
                continue
            # Exclude IDE subagent plugins and external skill vendor trees
            if ".agents" in rel.parts and "vibetrace-navigator" not in rel.parts:
                continue
            # Exclude historical scratch review dumps in docs/
            if rel.parts[0] == "docs" and len(rel.parts) == 2 and rel.name.lower() in {
                "aicode_review_coverage.md",
                "aicode_review-coverage.md",
                "aicode_review.md",
                "aicodereview-coverage.md",
                "audit.md",
                "audit_features.md",
            }:
                continue
            md_files.append(p)
        return md_files

    @classmethod
    def _sync_chunk_symbols(
        cls,
        conn: sqlite3.Connection,
        chunk: Any,
        rel_doc_path: str,
    ) -> int:
        """Indexes symbols from a single doc chunk into symbol_doc_links."""
        symbols = cls.extract_backticked_symbols(chunk.content)
        if not symbols:
            return 0

        chunk_tokens = estimate_tokens(chunk.content)
        created = 0
        for sym in set(symbols):
            cursor = conn.execute(
                "SELECT id, name, qualified_name FROM nodes "
                "WHERE name = ? OR qualified_name = ? OR qualified_name LIKE ? "
                "LIMIT 1",
                (sym, sym, f"%.{sym}"),
            )
            row = cursor.fetchone()
            if not row:
                continue

            sym_id = row["id"]
            sym_name = row["qualified_name"] or row["name"]
            conn.execute(
                """
                INSERT OR REPLACE INTO symbol_doc_links (
                    symbol_id, symbol_name, doc_path, heading,
                    heading_path, start_line, end_line, content, token_count
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    sym_id,
                    sym_name,
                    rel_doc_path,
                    chunk.title,
                    chunk.heading_path,
                    chunk.start_line,
                    chunk.end_line,
                    chunk.content,
                    chunk_tokens,
                ),
            )
            created += 1
        return created

    @classmethod
    def sync_links(cls, project_root: Path, db: IndexerDB) -> int:
        """Parses Markdown docs, matches AST symbols in SQLite, and persists links."""
        md_files = cls.find_markdown_files(project_root)
        total_links_created = 0

        with db._get_conn() as conn:
            conn.execute("DELETE FROM symbol_doc_links")

            for md_path in md_files:
                try:
                    rel_doc_path = str(md_path.relative_to(project_root)).replace("\\", "/")
                except ValueError:
                    rel_doc_path = str(md_path).replace("\\", "/")

                try:
                    content = md_path.read_text(encoding="utf-8", errors="replace")
                except OSError:
                    continue

                for chunk in chunk_markdown(content, max_chars=1200):
                    total_links_created += cls._sync_chunk_symbols(conn, chunk, rel_doc_path)

        return total_links_created

    @classmethod
    def get_linked_docs_for_symbol(
        cls,
        db: IndexerDB,
        symbol_name: str,
        symbol_id: str | None = None,
        max_tokens: int = 300,
    ) -> list[LinkedDocReference]:
        """Retrieves linked documentation sections enforcing a strict token budget."""
        results: list[LinkedDocReference] = []
        token_budget = max_tokens

        with db._get_conn() as conn:
            try:
                # Query by ID or by symbol name suffix
                cursor = conn.execute(
                    """
                    SELECT doc_path, heading, heading_path, start_line, end_line,
                           content, token_count
                    FROM symbol_doc_links
                    WHERE symbol_id = ? OR symbol_name = ? OR symbol_name LIKE ?
                    ORDER BY token_count ASC
                    LIMIT 3
                    """,
                    (symbol_id or "", symbol_name, f"%.{symbol_name}"),
                )
                rows = cursor.fetchall()
            except Exception:
                return []

            for r in rows:
                content = r["content"]
                cost = r["token_count"] or estimate_tokens(content)

                if token_budget < 20:
                    break

                # If single chunk exceeds remaining budget, truncate cleanly
                if cost > token_budget:
                    char_limit = token_budget * 4
                    content = (
                        content[:char_limit].rstrip()
                        + "\n... [Remaining architectural specification truncated for token budget]"
                    )
                    cost = token_budget

                results.append(
                    LinkedDocReference(
                        doc_path=r["doc_path"],
                        heading=r["heading"],
                        heading_path=r["heading_path"],
                        start_line=r["start_line"],
                        end_line=r["end_line"],
                        content=content,
                        token_count=cost,
                    )
                )
                token_budget -= cost

        return results

    @classmethod
    def _audit_line_symbols(
        cls,
        conn: sqlite3.Connection,
        line: str,
        line_idx: int,
        rel_doc_path: str,
    ) -> tuple[int, int, list[DocLinkAuditFinding]]:
        """Audits backticked symbols on a single line of Markdown."""
        symbols = cls.extract_backticked_symbols(line)
        if not symbols:
            return 0, 0, []

        referenced = len(symbols)
        valid = 0
        findings: list[DocLinkAuditFinding] = []

        for sym in symbols:
            row = conn.execute(
                "SELECT name, qualified_name FROM nodes "
                "WHERE name = ? OR qualified_name = ? OR qualified_name LIKE ? "
                "LIMIT 1",
                (sym, sym, f"%.{sym}"),
            ).fetchone()

            if row:
                valid += 1
                continue

            suggested: str | None = None
            clean_stem = sym.split(".")[-1]
            close = conn.execute(
                "SELECT qualified_name FROM nodes WHERE name LIKE ? LIMIT 1",
                (f"{clean_stem}%",),
            ).fetchone()
            if close:
                suggested = close[0]

            findings.append(
                DocLinkAuditFinding(
                    doc_path=rel_doc_path,
                    line_number=line_idx,
                    raw_symbol=sym,
                    issue=f"Symbol `{sym}` not found in codebase AST.",
                    suggested_symbol=suggested,
                )
            )

        return referenced, valid, findings

    @classmethod
    def audit_doc_links(cls, project_root: Path, db: IndexerDB) -> DocLinkAuditReport:
        """Audits all backticked symbols in documentation against the live AST symbol table."""
        md_files = cls.find_markdown_files(project_root)
        findings: list[DocLinkAuditFinding] = []
        total_referenced = 0
        valid_count = 0

        with db._get_conn() as conn:
            for md_path in md_files:
                try:
                    rel_doc_path = str(md_path.relative_to(project_root)).replace("\\", "/")
                except ValueError:
                    rel_doc_path = str(md_path).replace("\\", "/")

                try:
                    lines = md_path.read_text(encoding="utf-8", errors="replace").splitlines()
                except OSError:
                    continue

                for line_idx, line in enumerate(lines, start=1):
                    if line.strip().startswith(("```", "~~~")):
                        continue

                    ref_c, val_c, line_findings = cls._audit_line_symbols(
                        conn, line, line_idx, rel_doc_path
                    )
                    total_referenced += ref_c
                    valid_count += val_c
                    findings.extend(line_findings)

        broken_count = len(findings)
        return DocLinkAuditReport(
            total_doc_files=len(md_files),
            total_symbols_referenced=total_referenced,
            valid_links_count=valid_count,
            broken_links_count=broken_count,
            passed=(broken_count == 0),
            findings=findings,
        )
