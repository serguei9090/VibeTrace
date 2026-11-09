"""Unit, parity, and regression tests for Phase 5 Hybrid Rust Engine (`vibetrace._rust_core`)."""

from __future__ import annotations

from pathlib import Path

from vibetrace.indexer.db import IndexerDB
from vibetrace.indexer.main import index_file, reindex_all
from vibetrace.indexer.resolver import ModuleResolver
from vibetrace.indexer.rust_bridge import (
    extract_batch_native,
    get_native_version,
    is_native_available,
    scan_workspace_native,
)


def test_rust_core_extension_loaded() -> None:
    """Verify the compiled PyO3 Rust extension (`vibetrace._rust_core`) is active."""
    assert is_native_available() is True
    ver = get_native_version()
    assert ver is not None
    assert len(ver) > 0


def test_rust_workspace_scanner_and_parallel_ast(tmp_path: Path) -> None:
    """Verify native Rust scanner filters .gitignore, computes SHA-256, and extracts AST."""
    (tmp_path / ".gitignore").write_text("ignored_folder/\n*.skip.py\n", encoding="utf-8")
    (tmp_path / "ignored_folder").mkdir()
    (tmp_path / "ignored_folder" / "hidden.py").write_text("def secret(): pass\n", encoding="utf-8")
    (tmp_path / "bad.skip.py").write_text("def skip_me(): pass\n", encoding="utf-8")

    src = tmp_path / "src"
    src.mkdir()
    calc_code = (
        "class Calculator:\n"
        '    """Math helper."""\n'
        "    def add(self, a: int, b: int) -> int:\n"
        "        return helper_sum(a, b)\n"
    )
    (src / "calc.py").write_text(calc_code, encoding="utf-8")
    (src / "api.ts").write_text(
        "export function fetchItems(limit: number) {\n    return requestBackend(limit);\n}\n",
        encoding="utf-8",
    )

    scan_res = scan_workspace_native(tmp_path, tmp_path, known_hashes={}, force=False)
    assert scan_res is not None
    assert scan_res.total_seen == 2
    rel_paths = [d.rel_path for d in scan_res.dirty_files]
    assert rel_paths == ["src/api.ts", "src/calc.py"]

    # Verify parallel Rust Tree-sitter AST extraction
    extractions = extract_batch_native(scan_res.dirty_files)
    assert len(extractions) == 2
    calc_ext = next(e for e in extractions if e.rel_path == "src/calc.py")
    qnames = {n.qualified_name for n in calc_ext.nodes}
    assert {"calc", "Calculator", "Calculator.add"}.issubset(qnames)

    # Incremental second scan with known hashes should report 0 dirty and 2 unchanged
    known = {d.rel_path: d.content_hash for d in scan_res.dirty_files}
    known["src/deleted_file.py"] = "00001111"
    scan_res_2 = scan_workspace_native(tmp_path, tmp_path, known_hashes=known, force=False)
    assert scan_res_2 is not None
    assert len(scan_res_2.dirty_files) == 0
    assert scan_res_2.unchanged_files == ["src/api.ts", "src/calc.py"]
    assert scan_res_2.deleted_files == ["src/deleted_file.py"]


def test_rust_hybrid_vs_python_fallback_parity(tmp_path: Path) -> None:
    """Verify Hybrid Rust produces identical SQLite graph nodes & edges as Python fallback."""
    proj = tmp_path / "parity_repo"
    proj.mkdir()
    mod_a_code = (
        "from module_b import compute_value\n\n"
        "def entry_point(x: int) -> int:\n"
        "    return compute_value(x)\n"
    )
    (proj / "module_a.py").write_text(mod_a_code, encoding="utf-8")
    (proj / "module_b.py").write_text(
        "def compute_value(val: int) -> int:\n    return val * 2\n",
        encoding="utf-8",
    )

    # 1. Index using Hybrid Rust batch workspace engine
    db_rust = IndexerDB(str(tmp_path / "rust.db"))
    tel_rust = reindex_all(db_rust, str(proj), verbose=False, force=True, project_root=proj)
    assert tel_rust["engine"] == "rust-native"
    assert tel_rust["files_processed"] == 2

    # 2. Index individual files directly via single-file index_file + linker
    db_py = IndexerDB(str(tmp_path / "py.db"))
    resolver = ModuleResolver(proj)
    index_file(
        db_py, str(proj / "module_a.py"), project_root=proj, resolver=resolver, verbose=False
    )
    index_file(
        db_py, str(proj / "module_b.py"), project_root=proj, resolver=resolver, verbose=False
    )
    db_py.link_cross_file_edges()
    db_py.update_centrality_scores()

    node_sql = (
        "SELECT id, name, qualified_name, kind, file_path, start_line, end_line "
        "FROM nodes ORDER BY id"
    )
    edge_sql = (
        "SELECT from_node_id, to_node_id, relation_type, call_line "
        "FROM edges ORDER BY from_node_id, to_node_id"
    )

    # Compare extracted nodes and edges in both SQLite databases
    with db_rust._get_conn() as c_rust, db_py._get_conn() as c_py:
        rust_nodes = [dict(r) for r in c_rust.execute(node_sql).fetchall()]
        py_nodes = [dict(r) for r in c_py.execute(node_sql).fetchall()]
        assert rust_nodes == py_nodes

        rust_edges = [dict(r) for r in c_rust.execute(edge_sql).fetchall()]
        py_edges = [dict(r) for r in c_py.execute(edge_sql).fetchall()]
        assert rust_edges == py_edges

    db_rust.close()
    db_py.close()


def test_batch_save_workspace_index_deletes_and_updates(tmp_path: Path) -> None:
    """Verify single-transaction batch_save_workspace_index cleans up deleted files and symbols."""
    proj = tmp_path / "batch_repo"
    proj.mkdir()
    f1 = proj / "keep.py"
    f2 = proj / "remove_me.py"
    f1.write_text("def alpha(): pass\ndef beta(): alpha()\n", encoding="utf-8")
    f2.write_text("def obsolete_fn(): pass\n", encoding="utf-8")

    db = IndexerDB(str(tmp_path / "batch.db"))
    reindex_all(db, str(proj), verbose=False, force=True, project_root=proj)
    assert "remove_me.py" in db.get_all_indexed_file_paths()

    # Delete f2 and modify f1 (removing beta)
    f2.unlink()
    f1.write_text("def alpha(): pass\n", encoding="utf-8")

    reindex_all(db, str(proj), verbose=False, force=False, project_root=proj)
    indexed_after = db.get_all_indexed_file_paths()
    assert "remove_me.py" not in indexed_after
    assert "keep.py" in indexed_after

    with db._get_conn() as conn:
        names = {r[0] for r in conn.execute("SELECT name FROM nodes").fetchall()}
        assert "obsolete_fn" not in names
        assert "beta" not in names
        assert "alpha" in names

    db.close()
