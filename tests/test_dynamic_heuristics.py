"""Test dynamic dispatch heuristics for Python (getattr and dispatch dictionaries)."""

from __future__ import annotations

from vibetrace.indexer.parsers.python_parser import PythonParser


def test_python_getattr_and_dispatch_map_resolution() -> None:
    parser = PythonParser()
    code = b"""
def handle_alpha():
    pass

def handle_beta():
    pass

DISPATCH_MAP = {
    "alpha": handle_alpha,
    "beta": handle_beta,
}

def runner(client):
    res1 = getattr(client, "fetch_records")()
    res2 = getattr(client, "send_status")
"""
    nodes, edges = parser.extract("dispatcher.py", code)

    node_map = {n.name: n for n in nodes}
    assert "handle_alpha" in node_map
    assert "handle_beta" in node_map

    # Verify references edges from dispatch map
    ref_edges = [e for e in edges if e.relation_type == "references"]
    assert any(e.to_node_id == node_map["handle_alpha"].id for e in ref_edges)
    assert any(e.to_node_id == node_map["handle_beta"].id for e in ref_edges)

    # Verify calls edges from dynamic getattr calls
    expected_fetch = parser._get_id("dispatcher.py", "client.fetch_records")
    expected_send = parser._get_id("dispatcher.py", "client.send_status")
    call_edges = [e for e in edges if e.relation_type == "calls"]
    assert any(e.to_node_id == expected_fetch for e in call_edges)
    assert any(e.to_node_id == expected_send for e in call_edges)
