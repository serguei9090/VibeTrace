"""Unit tests for SCIP index importer."""

from pathlib import Path

from vibetrace.indexer.db import IndexerDB
from vibetrace.indexer.scip_importer import ScipImporter, parse_scip_symbol_name


def test_parse_scip_symbol_name():
    # Standard function
    name, qname = parse_scip_symbol_name(
        "scip-python python vibetrace 0.1.0 indexer/main/reindex_all()."
    )
    assert name == "reindex_all"
    assert "indexer.main.reindex_all" in qname

    # Method inside class
    name, qname = parse_scip_symbol_name(
        "scip-python python vibetrace 0.1.0 security/service/SecurityService#audit_project()."
    )
    assert name == "audit_project"
    assert "SecurityService.audit_project" in qname or "audit_project" in qname

    # Class symbol
    name, qname = parse_scip_symbol_name(
        "scip-python python vibetrace 0.1.0 security/service/SecurityService#"
    )
    assert name == "SecurityService"

    # Local symbol
    name, qname = parse_scip_symbol_name("local 42")
    assert name == "42"


def test_import_scip_json_dict(tmp_path: Path):
    db_path = tmp_path / "test_scip.db"
    db = IndexerDB(db_path)

    sample_scip_data = {
        "documents": [
            {
                "relative_path": "services/auth.py",
                "symbols": [
                    {
                        "symbol": "scip-python python myapp 1.0 services/auth/verify_token().",
                        "documentation": ["Verifies cryptographic JWT token authenticity."],
                    }
                ],
                "occurrences": [
                    {
                        "range": [9, 4, 15, 0],
                        "symbol": "scip-python python myapp 1.0 services/auth/verify_token().",
                        "symbol_roles": 1,  # Definition
                    }
                ],
            },
            {
                "relative_path": "api/routes.py",
                "occurrences": [
                    {
                        "range": [24, 8, 24, 20],
                        "symbol": "scip-python python myapp 1.0 services/auth/verify_token().",
                        "symbol_roles": 0,  # Reference / Call
                    }
                ],
            },
        ]
    }

    stats = ScipImporter.import_scip_json_dict(sample_scip_data, db)
    assert stats["files_processed"] == 2
    assert stats["nodes_imported"] == 1
    assert stats["edges_imported"] == 1

    # Verify nodes in database
    nodes = db.get_nodes_in_file("services/auth.py")
    assert len(nodes) == 1
    assert nodes[0]["name"] == "verify_token"
    assert "Verifies cryptographic JWT token" in (nodes[0]["docstring"] or "")

    # Verify edges in database
    with db._get_conn() as conn:
        rows = conn.execute("SELECT * FROM edges").fetchall()
        assert len(rows) == 1
        assert rows[0]["to_node_id"] == nodes[0]["id"]
        assert rows[0]["call_line"] == 25
