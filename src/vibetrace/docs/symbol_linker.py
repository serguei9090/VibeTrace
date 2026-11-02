"""Bi-directional Codebase Symbol <-> Documentation Linking Engine.

Maintains live connections between AST symbols and Markdown specifications,
enforces context window token budgets on code slices, and audits documentation
for broken symbol references (preventing architectural documentation drift).
"""

from __future__ import annotations

import re
from pathlib import Path

from pydantic import BaseModel, Field

from vibetrace.docs.chunker import chunk_markdown
from vibetrace.indexer.db import IndexerDB

# Filter out common programming keywords and language primitives
_DOC_SYMBOL_IGNORE = frozenset(
    {
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
        "get",
        "post",
        "put",
        "delete",
        "patch",
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
    "__pycache__",
    ".ruff_cache",
    ".pytest_cache",
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
            md_files.append(p)
        return md_files

    @classmethod
    def sync_links(cls, project_root: Path, db: IndexerDB) -> int:
        """Parses Markdown docs, matches AST symbols in SQLite, and persists links."""
        md_files = cls.find_markdown_files(project_root)
        total_links_created = 0

        with db._get_conn() as conn:
            # Clear previous symbol doc links
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

                chunks = chunk_markdown(content, max_chars=1200)

                for chunk in chunks:
                    symbols = cls.extract_backticked_symbols(chunk.content)
                    if not symbols:
                        continue

                    chunk_tokens = estimate_tokens(chunk.content)

                    for sym in set(symbols):
                        # Query symbol in AST nodes table
                        cursor = conn.execute(
                            "SELECT id, name, qualified_name FROM nodes "
                            "WHERE name = ? OR qualified_name = ? OR qualified_name LIKE ? "
                            "LIMIT 1",
                            (sym, sym, f"%.{sym}"),
                        )
                        row = cursor.fetchone()
                        if row:
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
                            total_links_created += 1

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
                    # Skip code blocks
                    if line.strip().startswith(("```", "~~~")):
                        continue

                    symbols = cls.extract_backticked_symbols(line)
                    for sym in symbols:
                        total_referenced += 1

                        # Query symbol existence in nodes
                        row = conn.execute(
                            "SELECT name, qualified_name FROM nodes "
                            "WHERE name = ? OR qualified_name = ? OR qualified_name LIKE ? "
                            "LIMIT 1",
                            (sym, sym, f"%.{sym}"),
                        ).fetchone()

                        if row:
                            valid_count += 1
                        else:
                            # Search closest fuzzy/prefix match in AST
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

        broken_count = len(findings)
        return DocLinkAuditReport(
            total_doc_files=len(md_files),
            total_symbols_referenced=total_referenced,
            valid_links_count=valid_count,
            broken_links_count=broken_count,
            passed=(broken_count == 0),
            findings=findings,
        )
