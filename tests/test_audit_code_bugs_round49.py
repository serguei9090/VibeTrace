"""Tests for Audit Round 49: Edge-Case Hardening for Documentation RAG."""

from __future__ import annotations

from pathlib import Path

import pytest
from pypdf import PdfWriter

from vibetrace.docs.chunker import chunk_markdown, chunk_pdf, chunk_text
from vibetrace.docs.db import DocDB
from vibetrace.docs.service import DocIndexService


@pytest.fixture
def edge_workspace(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """Setup isolated workspace for edge-case stress testing."""
    proj_root = tmp_path / "edge_proj"
    proj_root.mkdir()
    global_dir = tmp_path / "edge_global" / ".vibetrace"
    global_dir.mkdir(parents=True)
    monkeypatch.setenv("VIBETRACE_GLOBAL_ROOT", str(global_dir))

    docs_dir = proj_root / "docs"
    docs_dir.mkdir()
    return {"proj_root": proj_root, "docs_dir": docs_dir, "global_dir": global_dir}


def test_markdown_code_fence_with_hash_comments():
    """Verify that lines starting with # inside code blocks are not treated as Markdown headings."""
    content = (
        "# Top Level Title\n\n"
        "Here is some python code:\n\n"
        "```python\n"
        "# This is a python comment\n"
        "def hello():\n"
        "    # Another comment\n"
        "    return 'world'\n"
        "```\n\n"
        "## Next Real Section\n\n"
        "Section body text.\n"
    )
    chunks = chunk_markdown(content)
    # Must only have 2 real sections: Top Level Title and Next Real Section
    assert len(chunks) == 2
    assert chunks[0].heading_path == "Top Level Title"
    assert chunks[1].heading_path == "Top Level Title > Next Real Section"
    # The python comment must be part of section 1 content
    assert "# This is a python comment" in chunks[0].content


def test_chunk_text_oversized_single_line():
    """Verify that a single massive line without newlines is split into bounded chunks."""
    long_line = "WORD_" * 1000  # 5,000 characters
    chunks = chunk_text(long_line, max_chars=1000)
    assert len(chunks) >= 5
    for c in chunks:
        assert len(c.content) <= 1000
    assert chunks[0].start_line == 1
    assert chunks[-1].end_line == 1


def test_corrupt_and_encrypted_pdf_handling(tmp_path: Path):
    """Verify that corrupt, 0-byte, and password-encrypted PDFs do not raise exceptions."""
    # 1. 0-byte PDF
    zero_pdf = tmp_path / "zero.pdf"
    zero_pdf.write_bytes(b"")
    assert chunk_pdf(zero_pdf) == []

    # 2. Corrupted random bytes PDF
    junk_pdf = tmp_path / "junk.pdf"
    junk_pdf.write_bytes(b"NOT_A_REAL_PDF_HEADER_12345")
    assert chunk_pdf(junk_pdf) == []

    # 3. Password-encrypted PDF
    enc_pdf = tmp_path / "encrypted.pdf"
    writer = PdfWriter()
    writer.add_blank_page(width=100, height=100)
    writer.encrypt("secret_password")
    with open(enc_pdf, "wb") as f:
        writer.write(f)

    # Must safely decrypt with empty password or return [] without unhandled exception
    res = chunk_pdf(enc_pdf)
    assert isinstance(res, list)


def test_oversized_file_skipped_during_indexing(edge_workspace, monkeypatch: pytest.MonkeyPatch):
    """Verify that a file exceeding MAX_DOC_FILE_SIZE is safely skipped."""
    proj_root = edge_workspace["proj_root"]
    docs_dir = edge_workspace["docs_dir"]

    # Create dummy file that is slightly larger than max limit
    huge_file = docs_dir / "massive.txt"
    monkeypatch.setattr(DocIndexService, "MAX_DOC_FILE_SIZE", 1024)
    huge_file.write_text("A" * 2048, encoding="utf-8")  # 2 KB

    db = DocDB(proj_root / ".vibetrace" / "vibetrace.db")
    res = DocIndexService.index_catalog("test_cat", [str(docs_dir)], db, proj_root)
    # The file should be skipped
    assert res["files"] == 0
    assert res["chunks"] == 0


def test_duplicate_catalog_paths_deduplication(edge_workspace):
    """Verify that redundant or overlapping paths do not duplicate indexed chunks."""
    proj_root = edge_workspace["proj_root"]
    docs_dir = edge_workspace["docs_dir"]

    (docs_dir / "doc.md").write_text("# Title\n\nContent paragraph.", encoding="utf-8")

    db = DocDB(proj_root / ".vibetrace" / "vibetrace.db")
    # Pass docs_dir twice in paths list
    res = DocIndexService.index_catalog("cat_dedup", [str(docs_dir), str(docs_dir)], db, proj_root)
    # Must only index 1 file once
    assert res["files"] == 1
    assert res["chunks"] == 1
