"""Tests for Live Codebase Symbol <-> Documentation Bi-Directional Linking."""

import argparse
from pathlib import Path

from vibetrace.cli import build_parser
from vibetrace.cli_docs import _cmd_docs_check, _cmd_docs_sync_links
from vibetrace.docs.symbol_linker import SymbolDocLinker
from vibetrace.indexer.db import IndexerDB
from vibetrace.indexer.main import reindex_all
from vibetrace.mcp.formatters import format_code_slice_md
from vibetrace.services.trace_service import get_code_slice


def _setup_test_project(tmp_path: Path) -> IndexerDB:
    """Create a minimal project with Python code and Markdown documentation."""
    app_dir = tmp_path / "app"
    app_dir.mkdir()
    (app_dir / "__init__.py").write_text("", encoding="utf-8")

    (app_dir / "payment.py").write_text(
        "def process_payment(account_id: str, amount_cents: int) -> bool:\n"
        '    """Process customer transaction securely."""\n'
        "    return True\n\n"
        "class PaymentGateway:\n"
        "    def authorize_charge(self, token: str) -> bool:\n"
        "        return True\n",
        encoding="utf-8",
    )

    docs_dir = tmp_path / "docs"
    docs_dir.mkdir()
    (docs_dir / "architecture.md").write_text(
        "# Payment System Architecture\n\n"
        "## Transaction Processing\n"
        "All transactions flow through `process_payment` to enforce balance checks.\n"
        "The gateway invokes `authorize_charge` for token authorization.\n\n"
        "## Legacy Deprecated System\n"
        "Previously, the system relied on `old_unregistered_handler` which is now removed.\n"
        "Also note that standard values like `true` and `false` should be ignored.\n",
        encoding="utf-8",
    )

    (tmp_path / "README.md").write_text(
        "# Overview\n\n"
        "Welcome to the payment service. Call `PaymentGateway.authorize_charge` directly.\n",
        encoding="utf-8",
    )

    db = IndexerDB(str(tmp_path / "test_linking.db"))
    reindex_all(db, str(tmp_path), verbose=False)
    return db


def test_extract_backticked_symbols():
    """Verify that extract_backticked_symbols captures symbols and skips keywords."""
    content = """
    Here we use `process_payment` and `PaymentGateway.authorize_charge`.
    Skip primitives: `true`, `false`, `none`, `self`, `get`, `str`.
    Skip URLs or commands: `https://example.com` or `uv run test`.
    Skip numeric or symbols: `42` or `+/-`.
    ```python
    # Code blocks should be stripped
    def ignore_this():
        return True
    ```
    """
    symbols = SymbolDocLinker.extract_backticked_symbols(content)
    assert "process_payment" in symbols
    assert "PaymentGateway.authorize_charge" in symbols
    assert "true" not in symbols
    assert "false" not in symbols
    assert "none" not in symbols
    assert "ignore_this" not in symbols


def test_sync_and_query_symbol_doc_links(tmp_path: Path):
    """Verify sync_links indexes documents into SQLite and query retrieves them."""
    db = _setup_test_project(tmp_path)

    # Initially table is empty
    links = SymbolDocLinker.get_linked_docs_for_symbol(db, "process_payment")
    assert links == []

    # Sync links
    count = SymbolDocLinker.sync_links(tmp_path, db)
    assert count > 0

    # Query by symbol name
    linked = SymbolDocLinker.get_linked_docs_for_symbol(db, "process_payment")
    assert len(linked) >= 1
    ref = linked[0]
    assert "docs/architecture.md" in ref.doc_path.replace("\\", "/")
    assert "Transaction Processing" in ref.heading
    assert "process_payment" in ref.content

    # Query with max_tokens budget truncation
    tiny_budget = SymbolDocLinker.get_linked_docs_for_symbol(db, "process_payment", max_tokens=2)
    assert tiny_budget == []


def test_code_slice_with_linked_docs(tmp_path: Path):
    """Verify get_code_slice enriches CodeSliceResult with linked_docs and formats properly."""
    db = _setup_test_project(tmp_path)
    SymbolDocLinker.sync_links(tmp_path, db)

    # Slice with docs enabled
    res = get_code_slice(db, tmp_path, "process_payment", include_docs=True)
    assert res.found is True
    assert len(res.linked_docs) >= 1
    assert "Transaction Processing" in res.linked_docs[0].heading

    # Slice with docs disabled
    res_no_docs = get_code_slice(db, tmp_path, "process_payment", include_docs=False)
    assert res_no_docs.found is True
    assert len(res_no_docs.linked_docs) == 0

    # Test Markdown formatting of code slice
    md_output = format_code_slice_md(res)
    assert "### 📚 Linked Architectural Specifications" in md_output
    assert "Transaction Processing" in md_output
    assert "docs/architecture.md" in md_output.replace("\\", "/")


def test_audit_doc_links_detection(tmp_path: Path):
    """Verify audit_doc_links flags obsolete symbols and recognizes valid ones."""
    db = _setup_test_project(tmp_path)
    report = SymbolDocLinker.audit_doc_links(tmp_path, db)

    assert report.total_doc_files >= 2
    assert report.valid_links_count >= 2
    assert report.broken_links_count >= 1
    assert report.passed is False

    assert any(b.raw_symbol == "old_unregistered_handler" for b in report.findings)


def test_cli_docs_check_and_sync(tmp_path: Path, monkeypatch, capsys):
    """Verify CLI commands `vibetrace docs sync-links` and `vibetrace docs check`."""
    db = _setup_test_project(tmp_path)

    # Mock get_context_for_path to return our test db and root
    def mock_get_context(path_hint, auto_sync=True, root_override=None):
        return db, tmp_path

    monkeypatch.setattr("vibetrace.cli_docs.get_context_for_path", mock_get_context)

    # Run sync-links
    args_sync = argparse.Namespace(path=str(tmp_path), json=False, no_sync=True, root=None)
    ret_sync = _cmd_docs_sync_links(args_sync)
    assert ret_sync == 0
    out_sync, _ = capsys.readouterr()
    assert "Successfully synchronized" in out_sync

    # Run check
    args_check = argparse.Namespace(path=str(tmp_path), json=False, no_sync=True, root=None)
    ret_check = _cmd_docs_check(args_check)
    assert ret_check == 1  # Fails because of old_unregistered_handler
    out_check, _ = capsys.readouterr()
    assert "Documentation Symbol Reference Health Check" in out_check
    assert "old_unregistered_handler" in out_check


def test_cli_slice_arguments():
    """Verify CLI parser handles --no-docs and --max-doc-tokens on slice command."""
    parser = build_parser()
    args = parser.parse_args(["slice", "my_func", "--no-docs", "--max-doc-tokens", "150"])
    assert args.symbol == "my_func"
    assert args.no_docs is True
    assert args.max_doc_tokens == 150
