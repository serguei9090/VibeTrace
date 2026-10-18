from vibegraph.indexer.db import IndexerDB
from vibegraph.indexer.main import load_gitignore, reindex_all


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

    from vibegraph.indexer.watcher import start_observer

    db = IndexerDB(str(tmp_path / "test.db"))
    non_existent = tmp_path / "non_existent_folder"
    with pytest.raises(FileNotFoundError, match="Watcher target path does not exist"):
        start_observer(str(non_existent), db)
