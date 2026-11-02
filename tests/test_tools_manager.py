"""Tests for ToolsManager and CLI tools sub-engine management."""

from __future__ import annotations

import argparse
from pathlib import Path
from unittest.mock import MagicMock, patch

from vibetrace.cli_tools import cmd_tools
from vibetrace.tools_manager import ToolsManager


def test_tools_manager_get_status(tmp_path: Path):
    statuses = ToolsManager.get_status(tmp_path)
    assert "python" in statuses
    assert "typescript" in statuses
    assert "go" in statuses
    assert "rust" in statuses
    assert "php" in statuses
    assert "ruby" in statuses
    assert "shell" in statuses

    for lang, status in statuses.items():
        assert status.language == lang
        assert isinstance(status.is_available, bool)
        assert status.engine != ""


def test_cmd_tools_status_json(capsys, tmp_path: Path):
    args = argparse.Namespace(
        action="status",
        language="",
        path=str(tmp_path),
        json=True,
    )
    rc = cmd_tools(args)
    assert rc == 0
    captured = capsys.readouterr().out
    assert '"python":' in captured
    assert '"php":' in captured


def test_cmd_tools_status_text(capsys, tmp_path: Path):
    args = argparse.Namespace(
        action="status",
        language="",
        path=str(tmp_path),
        json=False,
    )
    rc = cmd_tools(args)
    assert rc == 0
    captured = capsys.readouterr().out
    assert "VibeTrace Polyglot Sub-Engine Status" in captured
    assert "PYTHON" in captured
    assert "PHP" in captured


def test_tools_manager_install_unsupported(tmp_path: Path):
    ok, msg = ToolsManager.install("brainfuck", tmp_path)
    assert not ok
    assert "Unknown language" in msg


@patch("urllib.request.urlretrieve")
def test_tools_manager_install_php_mocked(mock_retrieve: MagicMock, tmp_path: Path, monkeypatch):
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    ok, msg = ToolsManager.install("php", tmp_path)
    assert ok
    assert "phpstan.phar" in msg
    mock_retrieve.assert_called_once()
