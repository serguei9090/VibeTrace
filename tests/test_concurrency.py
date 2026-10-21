"""Verification of SQLite concurrency under simulated multi-agent swarm access."""

from __future__ import annotations

import concurrent.futures
from pathlib import Path

from vibetrace.indexer.db import Edge, IndexerDB, Node


def test_concurrent_swarm_writes(tmp_path: Path) -> None:
    """Simulate 10 concurrent AI agents performing simultaneous writes and reads."""
    db_file = tmp_path / "concurrent_swarm.db"
    db = IndexerDB(str(db_file))

    # Verify pragmas are active
    with db._get_conn() as conn:
        journal_mode = conn.execute("PRAGMA journal_mode").fetchone()[0]
        busy_timeout = conn.execute("PRAGMA busy_timeout").fetchone()[0]
        assert journal_mode.lower() == "wal"
        assert int(busy_timeout) >= 10000

    def agent_task(agent_id: int) -> int:
        local_db = IndexerDB(str(db_file))
        writes_done = 0
        for i in range(15):
            node = Node(
                id=f"agent_{agent_id}_node_{i}",
                name=f"agent_{agent_id}_func_{i}",
                qualified_name=f"Agent{agent_id}.func_{i}",
                kind="function",
                file_path=f"src/agent_{agent_id}.py",
                start_line=i * 10,
                end_line=i * 10 + 8,
            )
            local_db.upsert_node(node)
            local_db.upsert_edge(
                Edge(
                    from_node_id=node.id,
                    to_node_id="root_main",
                    relation_type="calls",
                    call_line=i * 10 + 2,
                )
            )
            local_db.set_meta(f"agent_{agent_id}_step", str(i))
            writes_done += 1

            # Interleaved concurrent reads
            nodes = local_db.search_lexical(f"func_{i}", limit=5)
            assert isinstance(nodes, list)
        return writes_done

    # Run 10 parallel worker threads simulating swarm agents
    with concurrent.futures.ThreadPoolExecutor(max_workers=10) as executor:
        futures = [executor.submit(agent_task, agent_id) for agent_id in range(10)]
        results = [f.result() for f in concurrent.futures.as_completed(futures)]

    assert len(results) == 10
    assert sum(results) == 150

    # Verify all 150 nodes are stored without corruption
    graph = db.get_graph_data()
    assert len(graph["nodes"]) == 150
    assert len(graph["edges"]) == 150
