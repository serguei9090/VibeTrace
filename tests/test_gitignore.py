from vibetrace.indexer.db import IndexerDB
from vibetrace.indexer.main import load_gitignore, reindex_all


def test_gitignore_loading(tmp_path):
    # Create a dummy .gitignore
    gitignore = tmp_path / ".gitignore"
    gitignore.write_text("*.ignore\nignored_dir/", encoding="utf-8")

    spec = load_gitignore(tmp_path)
    assert spec is not None
    assert spec.match_file("file.ignore")
    assert spec.match_file("ignored_dir/file.txt")
    assert not spec.match_file("file.keep")


def test_reindex_respects_gitignore(tmp_path, monkeypatch):
    # Setup directories and files
    (tmp_path / ".gitignore").write_text("ignore_me.py", encoding="utf-8")
    (tmp_path / "keep_me.py").write_text("def keep(): pass", encoding="utf-8")
    (tmp_path / "ignore_me.py").write_text("def ignore(): pass", encoding="utf-8")

    # Use valid file path for DB, not :memory: to persist schema
    db_path = tmp_path / "test.db"
    db = IndexerDB(str(db_path))

    # Run reindex
    # We need to change cwd or pass absolute path? reindex_all takes target_path_str
    # and load_gitignore uses strict path logic.

    # To test fully, we rely on reindex_all using load_gitignore(target_path)
    reindex_all(db, str(tmp_path), verbose=False)

    # Check what was indexed
    with db._get_conn() as conn:
        cursor = conn.execute("SELECT name FROM nodes")
        names = [row[0] for row in cursor.fetchall()]

    assert "keep" in names
    assert "ignore" not in names


def test_start_observer_missing_directory_raises(tmp_path):
    import pytest

    from vibetrace.indexer.watcher import start_observer

    db = IndexerDB(str(tmp_path / "test.db"))
    non_existent = tmp_path / "non_existent_folder"
    with pytest.raises(FileNotFoundError, match="Watcher target path does not exist"):
        start_observer(str(non_existent), db)


def test_watcher_on_moved_event(tmp_path):
    from watchdog.events import FileCreatedEvent, FileMovedEvent

    from vibetrace.indexer.watcher import CodeChangeHandler

    db_path = tmp_path / "test.db"
    db = IndexerDB(str(db_path))

    src_file = tmp_path / "old_module.py"
    src_file.write_text("def old_func(): pass\n", encoding="utf-8")
    dest_file = tmp_path / "new_module.py"
    dest_file.write_text("def new_func(): pass\n", encoding="utf-8")

    handler = CodeChangeHandler(db, root_path=str(tmp_path))
    # Index old_module initially
    handler.on_created(FileCreatedEvent(str(src_file)))

    nodes_before = db.find_nodes_by_name("old_func")
    assert len(nodes_before) == 1
    assert "old_module.py" in handler.resolver.known_files

    # Simulate atomic move / rename
    move_event = FileMovedEvent(str(src_file), str(dest_file))
    handler.on_moved(move_event)

    # old_func should be cleared, new_func should be indexed
    nodes_old = db.find_nodes_by_name("old_func")
    assert len(nodes_old) == 0
    nodes_new = db.find_nodes_by_name("new_func")
    assert len(nodes_new) == 1
    assert "old_module.py" not in handler.resolver.known_files
    assert "new_module.py" in handler.resolver.known_files


def test_reindex_prefix_collision_purge_safety(tmp_path):
    db_path = tmp_path / "test.db"
    db = IndexerDB(str(db_path))

    sub_dir = tmp_path / "src" / "app"
    sub_dir.mkdir(parents=True)
    (sub_dir / "main.py").write_text("def run_main(): pass\n", encoding="utf-8")

    sibling_dir = tmp_path / "src" / "app_helper"
    sibling_dir.mkdir(parents=True)
    (sibling_dir / "util.py").write_text("def run_util(): pass\n", encoding="utf-8")

    # Index both
    reindex_all(db, str(tmp_path), verbose=False)
    assert len(db.find_nodes_by_name("run_main")) == 1
    assert len(db.find_nodes_by_name("run_util")) == 1

    # Now reindex ONLY the sub_dir ("src/app")
    reindex_all(db, str(sub_dir), project_root=tmp_path, verbose=False)

    # run_util in sibling dir "src/app_helper" must NOT be purged
    assert len(db.find_nodes_by_name("run_main")) == 1
    assert len(db.find_nodes_by_name("run_util")) == 1


def test_sqlite_parameter_bound_safety(tmp_path):
    from vibetrace.indexer.models import Edge, Node

    db_path = tmp_path / "test.db"
    db = IndexerDB(str(db_path))

    # Create 1200 nodes in a single file to exceed SQLite 999 parameter limit
    num_nodes = 1200
    file_rel = "large_module.py"
    nodes = [
        Node(
            id=f"{file_rel}::func_{i}",
            name=f"func_{i}",
            qualified_name=f"func_{i}",
            kind="function",
            file_path=file_rel,
            start_line=i,
            end_line=i + 1,
        )
        for i in range(num_nodes)
    ]
    edges = [
        Edge(
            from_node_id=nodes[i].id,
            to_node_id=nodes[i + 1].id,
            relation_type="calls",
            call_line=i,
        )
        for i in range(num_nodes - 1)
    ]
    db.batch_save_file_index(file_rel, "hash123", nodes, edges)

    # Test get_graph_data with file_path filter (>1000 nodes & edges)
    graph = db.get_graph_data(file_path=file_rel)
    assert len(graph["nodes"]) == num_nodes
    assert len(graph["edges"]) == num_nodes - 1

    # Test get_nodes_by_ids with 1200 IDs (must chunk properly without exceeding SQLite max vars)
    all_ids = [n.id for n in nodes]
    fetched = db.get_nodes_by_ids(all_ids)
    assert len(fetched) == num_nodes
