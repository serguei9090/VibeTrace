"""Tests for SQLite Concurrency, BEGIN IMMEDIATE Transactions & Backoff (Audit Round 45)."""

from __future__ import annotations

import concurrent.futures
from pathlib import Path

from vibetrace.indexer.db import Edge, IndexerDB, Node


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
