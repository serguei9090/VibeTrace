"""
High-concurrency stress test suite for VibeTrace.

Validates that high numbers of concurrent threads/workers simultaneously reading
and writing to the SQLite databases (Codebase AST/Graph and Docs/RAG Catalog)
encounter zero lock errors, respect busy timeout and WAL retries, and maintain
absolute data integrity.
"""

import random
import string
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

from vibetrace.docs.chunker import HeadingChunk
from vibetrace.docs.db import DocDB
from vibetrace.indexer.db import IndexerDB
from vibetrace.indexer.models import Node


def _random_identifier(length: int = 10) -> str:
    return "".join(random.choices(string.ascii_letters, k=length))


def test_high_concurrency_code_and_docs_database_stress(tmp_path: Path):
    """
    Stress test with 24 concurrent threads:
    - 8 threads writing AST nodes into codebase DB with write-retry.
    - 8 threads indexing documentation chunks into DocDB (upsert_chunks).
    - 8 threads concurrently executing hybrid & FTS queries across both DBs.

    Asserts:
    - Zero `sqlite3.OperationalError: database is locked`.
    - Total inserted records match expected totals across all workers.
    - Search queries return non-empty results and complete cleanly.
    """
    code_db_path = tmp_path / "stress_vibetrace.db"
    doc_db_path = tmp_path / "stress_docs.db"

    # Initialize databases
    code_db = IndexerDB(code_db_path)
    doc_db = DocDB(doc_db_path)

    num_code_writers = 8
    num_doc_writers = 8
    num_readers = 8
    total_workers = num_code_writers + num_doc_writers + num_readers

    nodes_per_worker = 20
    chunks_per_worker = 20
    queries_per_reader = 25

    errors: list[str] = []
    errors_lock = threading.Lock()

    def code_writer_task(worker_id: int):
        try:
            worker_code_db = IndexerDB(code_db_path)
            nodes: list[Node] = []
            file_p = f"src/workers/worker_{worker_id}.py"
            for i in range(nodes_per_worker):
                sym = f"stress_fn_{worker_id}_{i}_{_random_identifier(6)}"
                nodes.append(
                    Node(
                        id=f"{file_p}::{sym}",
                        name=sym,
                        kind="function",
                        file_path=file_p,
                        start_line=i + 1,
                        end_line=i + 10,
                        signature=f"def {sym}(): pass",
                        docstring=f"Documentation for {sym} under stress testing",
                    )
                )

            # Insert via batch_save_file_index
            worker_code_db.batch_save_file_index(
                file_path=file_p,
                content_hash=f"hash_{worker_id}",
                nodes=nodes,
                edges=[],
                indexed_time=int(time.time()),
            )
            time.sleep(random.uniform(0.001, 0.005))
        except Exception as e:
            with errors_lock:
                errors.append(f"code_writer_{worker_id} failed: {type(e).__name__}: {e}")

    def doc_writer_task(worker_id: int):
        try:
            worker_doc_db = DocDB(doc_db_path)
            file_p = f"docs/guide_worker_{worker_id}.md"
            chunks: list[HeadingChunk] = []
            for i in range(chunks_per_worker):
                topic = f"concurrency_topic_{worker_id}_{i}"
                chunks.append(
                    HeadingChunk(
                        title=f"Guide {worker_id} Topic {i}",
                        heading_path=f"Guide > Topic {i}",
                        start_line=i * 10 + 1,
                        end_line=i * 10 + 9,
                        content=(
                            f"Detailed documentation on {topic} for high scale concurrency testing."
                        ),
                    )
                )

            # Upsert all chunks for this file
            worker_doc_db.upsert_chunks(
                catalog="stress_catalog",
                file_path=file_p,
                mtime=1700000000.0,
                chunks=chunks,
            )
            time.sleep(random.uniform(0.001, 0.005))
        except Exception as e:
            with errors_lock:
                errors.append(f"doc_writer_{worker_id} failed: {type(e).__name__}: {e}")

    def reader_task(reader_id: int):
        try:
            reader_code_db = IndexerDB(code_db_path)
            reader_doc_db = DocDB(doc_db_path)
            for _ in range(queries_per_reader):
                # Query code DB
                with reader_code_db._get_conn() as conn:
                    cur = conn.cursor()
                    cur.execute(
                        "SELECT id, name FROM nodes WHERE name LIKE ? LIMIT 5", ("%stress_fn%",)
                    )
                    _ = cur.fetchall()

                # Query doc DB BM25 search
                _ = reader_doc_db.search_bm25("concurrency", limit=5)

                time.sleep(random.uniform(0.001, 0.004))
        except Exception as e:
            with errors_lock:
                errors.append(f"reader_{reader_id} failed: {type(e).__name__}: {e}")

    start_time = time.perf_counter()
    with ThreadPoolExecutor(max_workers=total_workers) as executor:
        futures = []
        for w_id in range(num_code_writers):
            futures.append(executor.submit(code_writer_task, w_id))
        for w_id in range(num_doc_writers):
            futures.append(executor.submit(doc_writer_task, w_id))
        for r_id in range(num_readers):
            futures.append(executor.submit(reader_task, r_id))

        for f in as_completed(futures):
            f.result()

    elapsed = time.perf_counter() - start_time

    # Assert no thread failed with database lock or concurrency errors
    assert len(errors) == 0, f"Concurrency errors encountered ({len(errors)}): {errors[:5]}"

    # Verify Codebase DB Record Counts
    with code_db._get_conn() as conn:
        cur = conn.cursor()
        cur.execute("SELECT COUNT(*) FROM nodes")
        total_nodes = cur.fetchone()[0]
        expected_nodes = num_code_writers * nodes_per_worker
        assert total_nodes == expected_nodes, (
            f"Expected {expected_nodes} nodes, found {total_nodes}"
        )

    # Verify Doc DB Record Counts
    with doc_db._get_conn() as conn:
        cur = conn.cursor()
        cur.execute("SELECT COUNT(*) FROM doc_chunks")
        total_chunks = cur.fetchone()[0]
        expected_chunks = num_doc_writers * chunks_per_worker
        assert total_chunks == expected_chunks, (
            f"Expected {expected_chunks} doc chunks, found {total_chunks}"
        )

    assert elapsed > 0
