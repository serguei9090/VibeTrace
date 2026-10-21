"""Tests verifying fixes for Round 6 strict code bug critique audit."""

from pathlib import Path
from unittest.mock import MagicMock, patch

from vibetrace.indexer.main import main as indexer_main
from vibetrace.indexer.parser import ParserFactory
from vibetrace.indexer.parsers.python_parser import PythonParser
from vibetrace.indexer.parsers.ts_parser import TypeScriptParser
from vibetrace.indexer.resolver import ModuleResolver
from vibetrace.indexer.watcher import start_watcher_blocking


def test_ts_as_expression_unwrapping_resolves_method_call():
    """Bug 1: Verify TypeScript parser unwraps as_expression to track variable types."""
    code = b"""
    interface IWorker {
        work(): void;
    }
    class ConcreteWorker implements IWorker {
        work(): void {}
    }

    function run() {
        const w1 = new ConcreteWorker() as IWorker;
        w1.work();

        const w2 = new ConcreteWorker() as any;
        w2.work();
    }
    """
    parser = TypeScriptParser("typescript")
    _nodes, edges = parser.extract("src/app.ts", code)

    call_edges = [e for e in edges if e.relation_type == "calls"]
    assert len(call_edges) == 2
    # w1 resolved via IWorker.work, w2 resolved via unwrapped ConcreteWorker.work
    target_ids = [e.to_node_id for e in call_edges]
    assert parser._get_id("src/app.ts", "IWorker.work") in target_ids
    assert parser._get_id("src/app.ts", "ConcreteWorker.work") in target_ids


def test_parser_factory_supplies_resolver_to_ruby_and_php():
    """Bug 2: Verify ParserFactory binds resolver to Ruby and PHP parsers."""
    mock_resolver = MagicMock(spec=ModuleResolver)

    rb_parser = ParserFactory.get_parser("lib/service.rb", resolver=mock_resolver)
    assert rb_parser is not None
    assert rb_parser.resolver == mock_resolver

    php_parser = ParserFactory.get_parser("src/index.php", resolver=mock_resolver)
    assert php_parser is not None
    assert php_parser.resolver == mock_resolver


def test_module_resolver_handles_path_aliases(tmp_path: Path):
    """Bug 3: Verify ModuleResolver resolves @/ and ~/ TypeScript path aliases."""
    btn_file = tmp_path / "src" / "components" / "ui" / "button.tsx"
    btn_file.parent.mkdir(parents=True)
    btn_file.write_text("export function Button() {}\n", encoding="utf-8")

    view_file = tmp_path / "src" / "views" / "home.tsx"
    view_file.parent.mkdir(parents=True)
    view_file.write_text("import { Button } from '@/components/ui/button';\n", encoding="utf-8")

    resolver = ModuleResolver(tmp_path)

    # Test @/ alias
    resolved_at = resolver.resolve("@/components/ui/button", "src/views/home.tsx")
    assert resolved_at == "src/components/ui/button.tsx"

    # Test ~/ alias
    resolved_tilde = resolver.resolve("~/components/ui/button", "src/views/home.tsx")
    assert resolved_tilde == "src/components/ui/button.tsx"


def test_python_walrus_operator_assignment_type_resolution():
    """Bug 4: Verify Python parser tracks local types assigned via walrus operator (:=)."""
    code = b"""
    class Processor:
        def process(self):
            pass

    def execute():
        if (p := Processor()):
            p.process()
    """
    parser = PythonParser()
    _nodes, edges = parser.extract("src/proc.py", code)

    call_edges = [e for e in edges if e.relation_type == "calls"]
    expected_target = parser._get_id("src/proc.py", "Processor.process")
    assert any(e.to_node_id == expected_target for e in call_edges)


def test_indexer_entry_points_resolve_context_db(tmp_path: Path):
    """Bug 5: Verify indexer main and watcher use get_context_for_path to target project DB."""
    project_dir = tmp_path / "my_project"
    project_dir.mkdir()
    (project_dir / "pyproject.toml").write_text("[project]\nname='test'\n", encoding="utf-8")

    mock_db = MagicMock()
    mock_db.db_path = str(project_dir / ".vibetrace" / "vibetrace.db")

    mock_ctx_patch = patch(
        "vibetrace.services.graph_service.get_context_for_path",
        return_value=(mock_db, project_dir),
    )
    with mock_ctx_patch as mock_ctx:
        with patch("vibetrace.indexer.main.reindex_all") as mock_reindex:
            with patch("sys.argv", ["indexer_main", str(project_dir)]):
                indexer_main()
                mock_ctx.assert_called_once_with(str(project_dir), auto_sync=False)
                mock_reindex.assert_called_once_with(
                    mock_db, str(project_dir), project_root=project_dir
                )

    with mock_ctx_patch as mock_ctx_w:
        with patch("vibetrace.indexer.watcher.start_observer") as mock_obs:
            with patch("time.sleep", side_effect=KeyboardInterrupt):
                start_watcher_blocking(str(project_dir))
                mock_ctx_w.assert_called_once_with(str(project_dir), auto_sync=False)
                mock_obs.assert_called_once_with(str(project_dir), mock_db)
