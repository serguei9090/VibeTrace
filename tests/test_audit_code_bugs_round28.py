"""Tests verifying fixes for audit findings in Round 28."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

from fastapi.testclient import TestClient

from vibetrace.config import load_config, save_default_config
from vibetrace.indexer.db import IndexerDB
from vibetrace.indexer.hooks import _clean_lefthook_content, _inject_lefthook_hook
from vibetrace.indexer.parsers.go_parser import GoParser
from vibetrace.indexer.resolver import ModuleResolver
from vibetrace.indexer.watcher import start_observer, stop_observer
from vibetrace.server_api import app


def test_stop_observer_cancels_debounce_timer(tmp_path: Path):
    """Verify stop_observer cancels in-flight debounce timer and stops observer."""
    db = IndexerDB(str(tmp_path / "test.db"))
    observer = start_observer(str(tmp_path), db)
    assert hasattr(observer, "_event_handler")

    # Simulate pending timer
    mock_timer = MagicMock()
    observer._event_handler._debounce_timer = mock_timer

    stop_observer(observer)
    mock_timer.cancel.assert_called_once()
    assert observer._event_handler._debounce_timer is None


def test_server_api_call_tree_normalizes_file_path(tmp_path: Path, monkeypatch):
    """Verify /call-tree endpoint normalizes file_path parameter before building tree."""
    from vibetrace import server_api

    captured_file_path = []

    def mock_build_call_tree(db, symbol, max_depth=3, relations=None, file_path=None):
        captured_file_path.append(file_path)
        res = MagicMock()
        res.model_dump.return_value = {"root_symbol": symbol, "found": True}
        return res

    monkeypatch.setattr(server_api, "build_call_tree", mock_build_call_tree)
    monkeypatch.setattr(server_api, "project_root", tmp_path)

    client = TestClient(app)
    # Pass Windows-style relative path with leading ./ and backslash
    res = client.get("/call-tree?symbol=worker&file_path=.\\src\\worker.py")
    assert res.status_code == 200
    assert len(captured_file_path) == 1
    assert captured_file_path[0] == "src/worker.py"


def test_go_parser_embedded_struct_with_tags(tmp_path: Path):
    """Verify Go struct embedding with struct tags correctly creates inherits edge."""
    code = b"""package main

type BaseService struct {
    ID string
}

type UserService struct {
    *BaseService `json:"-"`
    Name string
}
"""
    resolver = ModuleResolver(tmp_path)
    parser = GoParser(resolver=resolver)
    nodes, edges = parser.extract("main.go", code)

    user_node = next((n for n in nodes if n.name == "UserService"), None)
    base_node = next((n for n in nodes if n.name == "BaseService"), None)
    assert user_node is not None
    assert base_node is not None

    inherits_edges = [
        e for e in edges if e.from_node_id == user_node.id and e.relation_type == "inherits"
    ]
    assert len(inherits_edges) == 1
    assert inherits_edges[0].to_node_id == base_node.id


def test_config_discovery_file_path_defense(tmp_path: Path):
    """Verify load_config and save_default_config handle file paths safely."""
    dummy_file = tmp_path / "src" / "main.py"
    dummy_file.parent.mkdir(parents=True)
    dummy_file.write_text("print('hello')", encoding="utf-8")

    # load_config with a file path should use parent directory
    cfg = load_config(dummy_file)
    assert cfg.project.name == "src"

    # save_default_config with a file path should create .vibetrace.toml in parent
    saved = save_default_config(dummy_file, project_name="my_service")
    assert saved == tmp_path / "src" / ".vibetrace.toml"
    assert saved.is_file()


def test_lefthook_injection_and_clean_keys():
    """Verify lefthook injection and cleaning avoids duplicate keys."""
    raw_content = """pre-commit:
  commands:
    lint:
      run: ruff check .

post-commit:
  commands:
"""
    injected = _inject_lefthook_hook(raw_content, "post-commit")
    assert "vibetrace-sync:" in injected
    assert injected.count("post-commit:") == 1

    cleaned = _clean_lefthook_content(injected)
    assert "vibetrace-sync" not in cleaned
