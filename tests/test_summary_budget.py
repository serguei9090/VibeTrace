"""Unit tests for token-budgeted repo map service."""

from pathlib import Path

from vibetrace.indexer.db import IndexerDB
from vibetrace.indexer.models import Node
from vibetrace.services.repo_map_service import RepoMapService, estimate_tokens


def test_budgeted_repo_map_respects_token_envelope(tmp_path: Path):
    db_path = tmp_path / "test_repo_map.db"
    db = IndexerDB(db_path)

    # Insert 20 nodes with varying centrality across two files
    for i in range(1, 21):
        fpath = "src/core.py" if i <= 10 else "src/utils.py"
        db.upsert_node(
            Node(
                id=f"{fpath}::func_{i}",
                name=f"func_{i}",
                qualified_name=f"func_{i}",
                kind="function",
                file_path=fpath,
                start_line=i * 10,
                end_line=i * 10 + 5,
                signature=f"func_{i}(arg: int) -> bool",
                centrality=float(i * 0.1),
            )
        )

    # Test small budget: 150 tokens
    res_small = RepoMapService.generate_budgeted_repo_map(db, budget_tokens=150)
    assert res_small["total_symbols"] == 20
    assert res_small["included_symbols"] < 20
    assert res_small["pruned_symbols"] > 0
    assert res_small["estimated_tokens"] <= 150
    assert "lower-centrality symbols pruned" in res_small["formatted_map"]

    # Test large budget: 4096 tokens
    res_large = RepoMapService.generate_budgeted_repo_map(db, budget_tokens=4096)
    assert res_large["total_symbols"] == 20
    assert res_large["included_symbols"] == 20
    assert res_large["pruned_symbols"] == 0


def test_estimate_tokens():
    text = "def hello_world(param1: str) -> bool:\n    return True\n"
    tokens = estimate_tokens(text)
    assert tokens > 0
    assert tokens < len(text)
