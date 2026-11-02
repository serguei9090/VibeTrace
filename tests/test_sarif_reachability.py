"""Unit tests for SARIF ingestion Call-Graph reachability filtering."""

from __future__ import annotations

import json
from pathlib import Path

from vibetrace.indexer.db import IndexerDB
from vibetrace.indexer.models import Edge, Node
from vibetrace.security.sarif_ingest import SarifIngestEngine
from vibetrace.services.review_service import format_unified_review_md, run_unified_review


def test_sarif_reachability_silences_dead_code(tmp_path: Path) -> None:
    db_file = tmp_path / "test.db"
    db = IndexerDB(str(db_file))

    # Setup indexed nodes and edges in SQLite
    # 1. Dead-code function in utils.py (0 callers)
    # 2. Reachable function in handler.py called by an entrypoint route
    db.upsert_node(
        Node(
            id="node_dead",
            name="unused_shell_exec",
            qualified_name="unused_shell_exec",
            kind="function",
            file_path="src/utils.py",
            start_line=10,
            end_line=20,
        )
    )
    db.upsert_node(
        Node(
            id="node_entry",
            name="api_endpoint",
            qualified_name="api_endpoint",
            kind="endpoint",
            file_path="src/api.py",
            start_line=1,
            end_line=15,
            decorators=["@app.get('/run')"],
        )
    )
    db.upsert_node(
        Node(
            id="node_target",
            name="execute_command",
            qualified_name="execute_command",
            kind="function",
            file_path="src/handler.py",
            start_line=5,
            end_line=25,
        )
    )
    db.upsert_edge(
        Edge(
            from_node_id="node_entry",
            to_node_id="node_target",
            relation_type="calls",
        )
    )

    sarif_data = {
        "$schema": "https://json.schemastore.org/sarif-2.1.0.json",
        "version": "2.1.0",
        "runs": [
            {
                "tool": {
                    "driver": {
                        "name": "sonarqube",
                        "rules": [
                            {
                                "id": "python:S2076",
                                "shortDescription": {
                                    "text": "OS commands should not be vulnerable"
                                },
                                "properties": {"cwe": ["CWE-78"]},
                            }
                        ],
                    }
                },
                "results": [
                    {
                        "ruleId": "python:S2076",
                        "level": "error",
                        "message": {"text": "OS command execution in unused helper"},
                        "locations": [
                            {
                                "physicalLocation": {
                                    "artifactLocation": {"uri": "src/utils.py"},
                                    "region": {"startLine": 12, "endLine": 12},
                                }
                            }
                        ],
                    },
                    {
                        "ruleId": "python:S2076",
                        "level": "error",
                        "message": {"text": "OS command execution in active handler"},
                        "locations": [
                            {
                                "physicalLocation": {
                                    "artifactLocation": {"uri": "src/handler.py"},
                                    "region": {"startLine": 10, "endLine": 10},
                                }
                            }
                        ],
                    },
                ],
            }
        ],
    }

    sarif_file = tmp_path / "scan.sarif"
    sarif_file.write_text(json.dumps(sarif_data), encoding="utf-8")

    findings = SarifIngestEngine.parse_sarif_file(sarif_file, tmp_path, db=db)
    assert len(findings) == 2

    dead_f = next(f for f in findings if f.file_path == "src/utils.py")
    assert dead_f.symbol_name == "unused_shell_exec"
    assert dead_f.metadata["reachability"] == "unreachable_dead_code"
    assert dead_f.metadata["is_reachable"] is False
    assert dead_f.metadata["reachability_silenced"] is True
    assert dead_f.status == "APPROVED"
    assert "[UNREACHABLE_DEAD_CODE]" in dead_f.title

    active_f = next(f for f in findings if f.file_path == "src/handler.py")
    assert active_f.symbol_name == "execute_command"
    assert active_f.metadata["reachability"] == "entrypoint_reachable"
    assert active_f.metadata["is_reachable"] is True
    assert active_f.metadata.get("reachability_silenced") is not True
    assert active_f.status == "UNAPPROVED"
    assert "api_endpoint" in active_f.message


def test_review_service_with_silenced_sarif(tmp_path: Path) -> None:
    db_file = tmp_path / "test2.db"
    db = IndexerDB(str(db_file))

    # Only dead-code finding
    db.upsert_node(
        Node(
            id="dead_1",
            name="dead_func",
            qualified_name="dead_func",
            kind="function",
            file_path="src/dormant.py",
            start_line=1,
            end_line=10,
        )
    )

    sarif_data = {
        "runs": [
            {
                "tool": {"driver": {"name": "semgrep", "rules": []}},
                "results": [
                    {
                        "ruleId": "dormant-rule",
                        "level": "error",
                        "message": {"text": "Dormant code finding"},
                        "locations": [
                            {
                                "physicalLocation": {
                                    "artifactLocation": {"uri": "src/dormant.py"},
                                    "region": {"startLine": 5, "endLine": 5},
                                }
                            }
                        ],
                    }
                ],
            }
        ]
    }
    sarif_file = tmp_path / "dormant.sarif"
    sarif_file.write_text(json.dumps(sarif_data), encoding="utf-8")

    # Run review with sarif
    report = run_unified_review(
        db=db,
        project_root=tmp_path,
        scope="full",
        check_dependencies=False,
        sarif_file=sarif_file,
    )
    assert report.silenced_sarif_count == 1
    assert report.unapproved_security_count == 0

    rendered = format_unified_review_md(report)
    assert "Silenced Dead-Code SAST" in rendered
    assert "1 alerts silenced" in rendered
