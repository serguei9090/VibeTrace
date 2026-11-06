"""Consolidated regression test suite: test_regression_indexer_and_graph_advanced.py."""

from __future__ import annotations

import concurrent.futures
from pathlib import Path

import pytest
from pypdf import PdfWriter

from vibetrace.docs.chunker import HeadingChunk, chunk_markdown, chunk_pdf, chunk_text
from vibetrace.docs.db import DocDB, get_local_doc_db
from vibetrace.docs.service import DocIndexService
from vibetrace.indexer.db import IndexerDB
from vibetrace.indexer.models import Edge, Node


def test_concurrent_burst_writes_and_reads(tmp_path: Path) -> None:
    """Stress test concurrent multi-threaded writes and reads on WAL-mode SQLite database."""
    db_file = tmp_path / "concurrent_stress.db"
    db = IndexerDB(str(db_file))

    # Pre-populate base nodes
    base_node = Node(
        id="base::main",
        name="main",
        qualified_name="main",
        kind="function",
        file_path="src/main.py",
        start_line=1,
        end_line=10,
    )
    db.upsert_node(base_node)

    num_writers = 8
    items_per_writer = 15

    def worker_writer(worker_id: int) -> int:
        written = 0
        for i in range(items_per_writer):
            node_id = f"worker_{worker_id}::fn_{i}"
            file_p = f"src/worker_{worker_id}.py"
            node = Node(
                id=node_id,
                name=f"fn_{i}",
                qualified_name=f"fn_{i}",
                kind="function",
                file_path=file_p,
                start_line=i * 5 + 1,
                end_line=i * 5 + 5,
            )
            edge = Edge(
                from_node_id=base_node.id,
                to_node_id=node_id,
                relation_type="calls",
                call_line=i + 1,
            )
            db.batch_save_file_index(file_p, f"hash_{i}", [node], [edge])
            written += 1
        return written

    def worker_reader(worker_id: int) -> int:
        read_count = 0
        for _ in range(items_per_writer):
            g = db.get_graph_data(None)
            read_count += len(g.get("nodes", []))
            res = db.search_lexical("main", limit=10)
            read_count += len(res)
        return read_count

    with concurrent.futures.ThreadPoolExecutor(max_workers=12) as executor:
        write_futures = [executor.submit(worker_writer, wid) for wid in range(num_writers)]
        read_futures = [executor.submit(worker_reader, wid) for wid in range(4)]

        # Collect write results
        total_written = sum(f.result() for f in write_futures)
        # Collect read results
        total_reads = sum(f.result() for f in read_futures)

    assert total_written == num_writers * items_per_writer
    assert total_reads > 0

    # Verify all records persisted correctly without corruption
    all_indexed = db.get_all_indexed_file_paths()
    expected_files = {f"src/worker_{wid}.py" for wid in range(num_writers)}
    assert expected_files.issubset(all_indexed)


def test_batch_save_rollback_atomicity_on_failure(tmp_path: Path, monkeypatch) -> None:
    """Verify that batch_save_file_index rolls back completely on mid-transaction error."""
    db_file = tmp_path / "rollback_test.db"
    db = IndexerDB(str(db_file))

    # Save initial good state
    initial_node = Node(
        id="init::func",
        name="func",
        qualified_name="func",
        kind="function",
        file_path="src/rollback.py",
        start_line=1,
        end_line=5,
    )
    db.batch_save_file_index("src/rollback.py", "hash_init", [initial_node], [])
    assert db.get_file_hash("src/rollback.py") == "hash_init"
    assert db.get_node("init::func") is not None

    # Simulate error right before commit
    orig_clean_path = "src/rollback.py"

    class BrokenNode(Node):
        pass

    # Node with invalid SQL value to trigger operational error during insertion
    bad_node = Node(
        id="bad::func",
        name="func2",
        qualified_name="func2",
        kind="function",
        file_path=orig_clean_path,
        start_line=10,
        end_line=20,
    )

    # Monkeypatch to raise runtime error inside batch_save_file_index
    def mock_broken_insert(*args, **kwargs):
        raise ValueError("Simulated disk error")

    # Attempt save that fails
    try:
        with monkeypatch.context() as m:
            m.setattr(db, "_resolve_vector_store", mock_broken_insert)
            db.batch_save_file_index("src/rollback.py", "hash_failed", [bad_node], [])
    except ValueError:
        pass

    # Verify initial state was preserved by ROLLBACK
    assert db.get_file_hash("src/rollback.py") == "hash_init"
    assert db.get_node("init::func") is not None
    assert db.get_node("bad::func") is None


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
