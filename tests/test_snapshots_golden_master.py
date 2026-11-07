import json
from pathlib import Path
from typing import Any

from vibetrace.indexer.parser import ParserFactory

FIXTURES_DIR = Path(__file__).parent / "fixtures" / "goldens"


def _normalize_ast_results(nodes: list[Any], edges: list[Any]) -> dict[str, Any]:
    """Serialize and sort nodes and edges into deterministic JSON-serializable dictionaries."""
    sorted_nodes = sorted(
        [
            {
                "name": n.name,
                "qualified_name": n.qualified_name or n.name,
                "kind": n.kind,
                "signature": n.signature,
                "docstring": n.docstring,
            }
            for n in nodes
        ],
        key=lambda x: (x["kind"], x["name"], x["signature"] or ""),
    )

    sorted_edges = sorted(
        [
            {
                "from_node_id": e.from_node_id,
                "to_node_id": e.to_node_id,
                "relation_type": e.relation_type,
            }
            for e in edges
        ],
        key=lambda x: (x["relation_type"], x["from_node_id"], x["to_node_id"]),
    )

    return {"nodes": sorted_nodes, "edges": sorted_edges}


def _verify_or_create_golden(sample_filename: str) -> None:
    source_file = FIXTURES_DIR / sample_filename
    golden_file = FIXTURES_DIR / f"{sample_filename}.golden.json"

    parser = ParserFactory.get_parser(sample_filename)
    assert parser is not None, f"Parser missing for {sample_filename}"

    code_bytes = source_file.read_bytes()
    nodes, edges = parser.extract(sample_filename, code_bytes)
    current_data = _normalize_ast_results(nodes, edges)

    if not golden_file.exists():
        # Baseline creation
        golden_file.write_text(json.dumps(current_data, indent=2), encoding="utf-8")
        return

    expected_data = json.loads(golden_file.read_text(encoding="utf-8"))
    assert current_data == expected_data, (
        f"Golden master snapshot mismatch for {sample_filename}. "
        f"If AST output changes intentionally, update {golden_file.name}."
    )


def test_golden_master_python():
    """Verify deterministic AST extraction snapshot for Python."""
    _verify_or_create_golden("python_sample.py")


def test_golden_master_typescript_tsx():
    """Verify deterministic AST extraction snapshot for TypeScript/TSX."""
    _verify_or_create_golden("ts_sample.tsx")


def test_golden_master_go():
    """Verify deterministic AST extraction snapshot for Go."""
    _verify_or_create_golden("go_sample.go")


def test_golden_master_rust():
    """Verify deterministic AST extraction snapshot for Rust."""
    _verify_or_create_golden("rust_sample.rs")
