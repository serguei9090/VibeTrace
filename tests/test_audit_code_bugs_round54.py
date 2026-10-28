"""Tests for Audit Round 54: Polyglot Parity and Disambiguation in Vibecoding."""

from __future__ import annotations

from pathlib import Path

from vibetrace.indexer.db import IndexerDB
from vibetrace.indexer.models import Edge, Node
from vibetrace.services.caller_guard import CallerGuardService, format_caller_guard_md
from vibetrace.services.vibe_bundle import VibeBundleService


def test_caller_guard_disambiguation_centrality(tmp_path: Path) -> None:
    """Ensure duplicate symbol names are disambiguated by caller count/centrality."""
    db_file = tmp_path / "test.db"
    db = IndexerDB(db_path=str(db_file))

    # Candidate 1: active worker implementation in src/worker.py (3 callers)
    worker_file = tmp_path / "src" / "worker.py"
    worker_file.parent.mkdir(parents=True, exist_ok=True)
    worker_file.write_text("def process_item(item):\n    return item\n", encoding="utf-8")

    node_active = Node(
        id="worker:process_item",
        name="process_item",
        qualified_name="process_item",
        kind="function",
        file_path="src/worker.py",
        start_line=1,
        end_line=2,
    )
    db.upsert_node(node_active)

    # Candidate 2: legacy uncalled implementation in src/legacy.py (0 callers)
    legacy_file = tmp_path / "src" / "legacy.py"
    legacy_file.write_text("def process_item(item):\n    pass\n", encoding="utf-8")

    node_legacy = Node(
        id="legacy:process_item",
        name="process_item",
        qualified_name="process_item",
        kind="function",
        file_path="src/legacy.py",
        start_line=1,
        end_line=2,
    )
    db.upsert_node(node_legacy)

    # Add 3 callers to worker:process_item
    for idx in range(1, 4):
        caller_nid = f"caller_{idx}:run"
        caller_file = tmp_path / "src" / f"caller_{idx}.py"
        caller_file.write_text("def run(): process_item(1)\n", encoding="utf-8")
        db.upsert_node(
            Node(
                id=caller_nid,
                name="run",
                qualified_name=f"caller_{idx}.run",
                kind="function",
                file_path=f"src/caller_{idx}.py",
                start_line=1,
                end_line=1,
            )
        )
        db.upsert_edge(
            Edge(
                from_node_id=caller_nid,
                to_node_id="worker:process_item",
                relation_type="calls",
                call_line=1,
            )
        )

    # 1. Query without file_path: should select src/worker.py
    report = CallerGuardService.get_caller_guard(db, tmp_path, "process_item")
    assert report.found is True
    assert report.file_path == "src/worker.py"
    assert report.total_callers == 3
    assert len(report.alternative_candidates) == 1
    assert "src/legacy.py" in report.alternative_candidates[0]
    assert "Disambiguation: Selected 'src/worker.py'" in report.safety_advisory

    md = format_caller_guard_md(report)
    assert "Alternate Symbols Named `process_item`:" in md
    assert "src/legacy.py:L1 (0 callers)" in md

    # 2. Query with explicit file_path: should strictly select src/legacy.py
    report_explicit = CallerGuardService.get_caller_guard(
        db, tmp_path, "process_item", file_path="src/legacy.py"
    )
    assert report_explicit.found is True
    assert report_explicit.file_path == "src/legacy.py"
    assert report_explicit.total_callers == 0
    assert len(report_explicit.alternative_candidates) == 0


def test_vibe_bundle_polyglot_typescript(tmp_path: Path) -> None:
    """Verify TypeScript code slicing and test pattern discovery with tsx/ts fences."""
    db_file = tmp_path / "test_ts.db"
    db = IndexerDB(db_path=str(db_file))

    # TypeScript source
    ts_file = tmp_path / "src" / "authService.ts"
    ts_file.parent.mkdir(parents=True, exist_ok=True)
    ts_file.write_text(
        "export function verifyToken(token: string): boolean {\n"
        "    return token.startsWith('Bearer ');\n"
        "}\n",
        encoding="utf-8",
    )

    db.upsert_node(
        Node(
            id="auth:verifyToken",
            name="verifyToken",
            qualified_name="verifyToken",
            kind="function",
            file_path="src/authService.ts",
            start_line=1,
            end_line=3,
            signature="(token: string): boolean",
        )
    )

    # TypeScript sibling test
    test_file = tmp_path / "src" / "authService.test.ts"
    test_file.write_text(
        "import { verifyToken } from './authService';\n"
        "describe('verifyToken', () => {\n"
        "    it('should validate bearer token', () => {\n"
        "        expect(verifyToken('Bearer xyz')).toBe(true);\n"
        "    });\n"
        "});\n",
        encoding="utf-8",
    )

    bundle = VibeBundleService.assemble_bundle(
        db, tmp_path, intent="token verification", target_symbol="verifyToken", token_budget=1200
    )
    assert bundle.target_symbol == "verifyToken"
    assert bundle.test_context is not None
    assert bundle.test_context.found is True
    assert bundle.test_context.test_file == "src/authService.test.ts"

    prompt = bundle.formatted_prompt
    assert "```typescript" in prompt
    assert "verifyToken" in prompt
    assert "should validate bearer token" in prompt


def test_vibe_bundle_polyglot_go(tmp_path: Path) -> None:
    """Verify Go code slicing and sibling test pattern discovery."""
    db_file = tmp_path / "test_go.db"
    db = IndexerDB(db_path=str(db_file))

    go_file = tmp_path / "pkg" / "hash.go"
    go_file.parent.mkdir(parents=True, exist_ok=True)
    go_file.write_text(
        "package pkg\n\nfunc HashSum(input string) string {\n    return input\n}\n",
        encoding="utf-8",
    )

    db.upsert_node(
        Node(
            id="pkg:HashSum",
            name="HashSum",
            qualified_name="pkg.HashSum",
            kind="function",
            file_path="pkg/hash.go",
            start_line=3,
            end_line=5,
            signature="(input string) string",
        )
    )

    go_test = tmp_path / "pkg" / "hash_test.go"
    go_test.write_text(
        'package pkg\n\nimport "testing"\n\n'
        'func TestHashSum(t *testing.T) {\n    _ = HashSum("abc")\n}\n',
        encoding="utf-8",
    )

    bundle = VibeBundleService.assemble_bundle(
        db, tmp_path, intent="hash string sum", target_symbol="HashSum", token_budget=1000
    )
    assert bundle.test_context is not None
    assert bundle.test_context.found is True
    assert bundle.test_context.test_file == "pkg/hash_test.go"
    assert bundle.test_context.test_function == "TestHashSum"

    prompt = bundle.formatted_prompt
    assert "```go" in prompt
