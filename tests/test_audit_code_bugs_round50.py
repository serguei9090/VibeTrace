"""Tests for Audit Round 50: Concurrency, Thread-Safety, and SQLite Lock Stress Testing."""

from __future__ import annotations

import concurrent.futures
from pathlib import Path

import pytest

from vibetrace.docs.chunker import HeadingChunk
from vibetrace.docs.db import DocDB, get_local_doc_db
from vibetrace.docs.service import DocIndexService


@pytest.fixture
def stress_workspace(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """Setup isolated workspace for concurrency stress testing."""
    proj_root = tmp_path / "stress_proj"
    proj_root.mkdir()
    global_dir = tmp_path / "stress_global" / ".vibetrace"
    global_dir.mkdir(parents=True)
    monkeypatch.setenv("VIBETRACE_GLOBAL_ROOT", str(global_dir))

    docs_dir = proj_root / "docs"
    docs_dir.mkdir()
    return {"proj_root": proj_root, "docs_dir": docs_dir, "global_dir": global_dir}


def test_concurrent_writes_and_reads_no_lock_errors(stress_workspace):
    """Verify that multiple concurrent threads writing and searching do not cause database locks."""
    proj_root = stress_workspace["proj_root"]
    docs_dir = stress_workspace["docs_dir"]
    db = DocDB(proj_root / ".vibetrace" / "vibetrace.db")

    # Create 10 different doc files
    num_writers = 10
    num_readers = 20

    for i in range(num_writers):
        (docs_dir / f"doc_{i}.md").write_text(
            f"# Document {i}\n\nArchitecture section for worker {i} and unique_token_{i}.",
            encoding="utf-8",
        )

    errors: list[Exception] = []

    def writer_task(worker_id: int):
        try:
            fpath = docs_dir / f"doc_{worker_id}.md"
            res = DocIndexService.index_catalog(
                "stress_cat",
                [str(fpath)],
                db,
                proj_root,
                force=True,
            )
            assert res["files"] == 1
        except Exception as e:
            errors.append(e)

    def reader_task(worker_id: int):
        try:
            # Query for words indexed by other workers
            target_id = worker_id % num_writers
            hits = db.search_bm25(f"unique_token_{target_id}", catalog="stress_cat")
            assert isinstance(hits, list)
        except Exception as e:
            errors.append(e)

    # Launch all writers and readers simultaneously in a thread pool
    with concurrent.futures.ThreadPoolExecutor(max_workers=16) as executor:
        futures = []
        for w in range(num_writers):
            futures.append(executor.submit(writer_task, w))
        for r in range(num_readers):
            futures.append(executor.submit(reader_task, r))

        for f in concurrent.futures.as_completed(futures):
            f.result()

    # Zero database lock errors allowed
    assert not errors, f"Encountered concurrency errors: {errors}"

    # Verify all 10 documents were successfully indexed
    summary = db.get_catalogs_summary()
    assert len(summary) == 1
    assert summary[0]["doc_count"] == num_writers


def test_atomic_file_upsert_no_vanishing_window(stress_workspace):
    """Verify that concurrent readers never observe 0 chunks during an active upsert."""
    proj_root = stress_workspace["proj_root"]
    db = DocDB(proj_root / ".vibetrace" / "vibetrace.db")

    chunks = [
        HeadingChunk(
            title="Section 1",
            heading_path="Doc > Section 1",
            start_line=1,
            end_line=10,
            content="Atomic chunk content payload",
        )
    ]

    # Pre-populate
    db.upsert_chunks("atomic_cat", "docs/atomic.md", 100.0, chunks)

    stop_flag = False
    observed_zero = False

    def reader_loop():
        nonlocal observed_zero
        while not stop_flag:
            res = db.search_bm25("Atomic", catalog="atomic_cat")
            if len(res) == 0:
                observed_zero = True
                break

    def writer_loop():
        for i in range(25):
            db.upsert_chunks("atomic_cat", "docs/atomic.md", 100.0 + i, chunks)

    with concurrent.futures.ThreadPoolExecutor(max_workers=4) as executor:
        r_future = executor.submit(reader_loop)
        w_future = executor.submit(writer_loop)
        w_future.result()
        stop_flag = True
        r_future.result()

    assert not observed_zero, "Reader observed 0 chunks during upsert window!"


def test_cached_db_instances_and_read_schema_no_lock(stress_workspace):
    """Verify get_local_doc_db caching and fast read-only schema checks."""
    proj_root = stress_workspace["proj_root"]
    db1 = get_local_doc_db(proj_root)
    db2 = get_local_doc_db(proj_root)
    assert db1 is db2

    # Schema init on an already existing DB must be a fast no-op without write lock
    db1._init_schema()
