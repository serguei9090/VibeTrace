"""Tests for Markdown glob pattern entries in documentation catalogs."""

from __future__ import annotations

from pathlib import Path

import pytest

from vibetrace.docs.db import DocDB
from vibetrace.docs.patterns import expand_pattern, is_pattern, pattern_matches
from vibetrace.docs.service import DocIndexService


@pytest.fixture
def tree(tmp_path: Path) -> Path:
    root = tmp_path / "proj"
    (root / "src" / "pkg").mkdir(parents=True)
    (root / "src" / "node_modules" / "dep").mkdir(parents=True)
    (root / "docs").mkdir()
    (root / "README.md").write_text("# Root\n\nroot readme\n", encoding="utf-8")
    (root / "src" / "README.md").write_text("# Src\n\nsrc readme\n", encoding="utf-8")
    (root / "src" / "pkg" / "readme.MD").write_text("# Pkg\n\npkg readme\n", encoding="utf-8")
    (root / "src" / "pkg" / "doc1.md").write_text("# Doc1\n\ncontent\n", encoding="utf-8")
    (root / "src" / "notes.txt").write_text("not markdown\n", encoding="utf-8")
    (root / "src" / "node_modules" / "dep" / "README.md").write_text(
        "# NM\n\nvendored\n", encoding="utf-8"
    )
    (root / "docs" / "guide.md").write_text("# Guide\n\nguide\n", encoding="utf-8")
    return root


def _db(root: Path) -> DocDB:
    return DocDB(root / ".vibetrace" / "vibetrace.db")


def _rel(f: Path, root: Path) -> str:
    return str(f.relative_to(root)).replace("\\", "/")


def _catalog_files(db: DocDB, catalog: str) -> set[str]:
    with db._get_conn() as conn:
        rows = conn.execute(
            "SELECT DISTINCT file_path FROM doc_chunks WHERE catalog = ?", (catalog,)
        ).fetchall()
    return {r[0] for r in rows}


def _chunk_count(db: DocDB, catalog: str) -> int:
    with db._get_conn() as conn:
        row = conn.execute(
            "SELECT COUNT(*) AS n FROM doc_chunks WHERE catalog = ?", (catalog,)
        ).fetchone()
    return int(row["n"])


def test_is_pattern_detection():
    assert is_pattern("src/**/README.md")
    assert is_pattern("./README*.md")
    assert is_pattern("docs/a[0].md")
    assert not is_pattern("docs/")
    assert not is_pattern("src/README.md")


def test_expand_pattern_case_insensitive_and_pruned(tree):
    files, warnings = expand_pattern("src/**/README.md", tree)
    assert sorted(_rel(f, tree) for f in files) == ["src/README.md", "src/pkg/readme.MD"]
    assert warnings == []


def test_globstar_matches_zero_segments(tree):
    files, _ = expand_pattern("**/README.md", tree)
    rels = sorted(_rel(f, tree) for f in files)
    assert rels == ["README.md", "src/README.md", "src/pkg/readme.MD"]


def test_star_does_not_cross_separator(tree):
    files, _ = expand_pattern("src/*.md", tree)
    assert [_rel(f, tree) for f in files] == ["src/README.md"]


def test_question_mark_segment(tree):
    files, _ = expand_pattern("src/*/doc?.md", tree)
    assert [_rel(f, tree) for f in files] == ["src/pkg/doc1.md"]


def test_non_markdown_not_selected(tree):
    files, _ = expand_pattern("src/**/*", tree)
    assert files
    assert all(f.suffix.casefold() in {".md", ".markdown"} for f in files)


def test_missing_base_warns(tree):
    files, warnings = expand_pattern("nope/**/README.md", tree)
    assert files == []
    assert any("does not exist" in w for w in warnings)


def test_dotdot_rejected(tree):
    files, warnings = expand_pattern("../**/*.md", tree)
    assert files == []
    assert any("not a valid pattern" in w for w in warnings)


def test_zero_match_warns(tree):
    files, warnings = expand_pattern("src/**/NOPE.md", tree)
    assert files == []
    assert any("matched 0" in w for w in warnings)


def test_index_pattern_catalog(tree):
    db = _db(tree)
    res = DocIndexService.index_catalog("egm", ["src/**/README.md"], db, tree)
    assert res["files"] == 2
    assert _catalog_files(db, "egm") == {"src/README.md", "src/pkg/readme.MD"}


def test_index_directory_entry_prunes_ignored_dirs(tree):
    db = _db(tree)
    DocIndexService.index_catalog("srcdir", ["src"], db, tree)
    files = _catalog_files(db, "srcdir")
    assert "src/README.md" in files
    assert not any("node_modules" in f for f in files)
    # .txt stays reachable through directory entries (SUPPORTED_DOC_EXTS)
    assert "src/notes.txt" in files


def test_deleted_file_chunks_removed(tree):
    db = _db(tree)
    DocIndexService.index_catalog("egm", ["src/**/*.md"], db, tree)
    assert "src/pkg/doc1.md" in _catalog_files(db, "egm")
    (tree / "src" / "pkg" / "doc1.md").unlink()
    res = DocIndexService.index_catalog("egm", ["src/**/*.md"], db, tree)
    assert "src/pkg/doc1.md" not in _catalog_files(db, "egm")
    assert any("removed 1 file" in w for w in res["warnings"])


def test_zero_match_all_pattern_catalog_keeps_rows(tree):
    db = _db(tree)
    DocIndexService.index_catalog("egm", ["src/**/README.md"], db, tree)
    before = _chunk_count(db, "egm")
    assert before > 0
    res = DocIndexService.index_catalog("egm", ["src/**/NOPE.md"], db, tree)
    assert _chunk_count(db, "egm") == before
    assert any("all entries are patterns" in w for w in res["warnings"])


def test_rerun_unchanged_no_duplicate_chunks(tree):
    db = _db(tree)
    DocIndexService.index_catalog("egm", ["src/**/README.md"], db, tree)
    first = _chunk_count(db, "egm")
    DocIndexService.index_catalog("egm", ["src/**/README.md"], db, tree)
    assert _chunk_count(db, "egm") == first


def test_changed_content_replaces_chunks(tree):
    db = _db(tree)
    DocIndexService.index_catalog("egm", ["src/**/README.md"], db, tree)
    content = "# Src v2\n\ncompletely different content\n"
    (tree / "src" / "README.md").write_text(content, encoding="utf-8")
    DocIndexService.index_catalog("egm", ["src/**/README.md"], db, tree, force=True)
    with db._get_conn() as conn:
        row = conn.execute(
            "SELECT content FROM doc_chunks WHERE catalog = ? AND file_path = ?",
            ("egm", "src/README.md"),
        ).fetchone()
    assert "completely different content" in row["content"]


def test_pattern_matches_basic(tree):
    assert pattern_matches("src/**/README.md", tree, tree / "src" / "pkg" / "readme.MD")
    assert pattern_matches("src/**/README.md", tree, tree / "src" / "README.md")
    assert not pattern_matches("src/**/README.md", tree, tree / "docs" / "guide.md")
    assert not pattern_matches("src/**/README.md", tree, tree / "src" / "notes.txt")
    ignored_readme = tree / "src" / "node_modules" / "dep" / "README.md"
    assert not pattern_matches("src/**/README.md", tree, ignored_readme)
    assert not pattern_matches("../**/*.md", tree, tree / "docs" / "guide.md")
    assert not pattern_matches("src/**/README.md", tree, tree / "README.md")


def test_pattern_matches_case_insensitive(tree):
    assert pattern_matches("SRC/**/readme.md", tree, tree / "src" / "README.md")
    assert pattern_matches("src/**/*.MD", tree, tree / "src" / "pkg" / "doc1.md")


def test_pattern_matches_no_glob_part(tree):
    assert not pattern_matches("src/docs", tree, tree / "docs" / "guide.md")
