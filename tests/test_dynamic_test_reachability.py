"""Tests for Dynamic Test Fixture Reachability (@pytest.mark.parametrize & fixtures)."""

from pathlib import Path

from vibetrace.indexer.db import IndexerDB
from vibetrace.indexer.parsers.js_parser import JavaScriptParser
from vibetrace.indexer.parsers.python_parser import PythonParser
from vibetrace.services.predictive_test_service import PredictiveTestService


def test_pytest_parametrize_function_matrix():
    parser = PythonParser()
    code = b"""
from app.validators import validate_email, validate_uuid

@pytest.mark.parametrize("validator", [
    validate_email,
    validate_uuid,
])
def test_all_validators(validator):
    assert validator("sample") is True
"""
    nodes, edges = parser.extract("tests/test_val.py", code)
    test_node = next(n for n in nodes if n.name == "test_all_validators")

    test_call_edges = [
        e for e in edges if e.from_node_id == test_node.id and e.relation_type == "calls"
    ]
    target_ids = {e.to_node_id for e in test_call_edges}

    email_id = parser._get_id("external", "validate_email")
    uuid_id = parser._get_id("external", "validate_uuid")
    assert email_id in target_ids
    assert uuid_id in target_ids


def test_pytest_parametrize_nested_tuples():
    parser = PythonParser()
    code = b"""
from app.validators import validate_email, validate_uuid

@pytest.mark.parametrize(("validator", "expected"), [
    (validate_email, True),
    (validate_uuid, False),
])
def test_nested_validators(validator, expected):
    assert validator("sample") == expected
"""
    nodes, edges = parser.extract("tests/test_nested.py", code)
    test_node = next(n for n in nodes if n.name == "test_nested_validators")

    test_call_edges = [
        e for e in edges if e.from_node_id == test_node.id and e.relation_type == "calls"
    ]
    target_ids = {e.to_node_id for e in test_call_edges}

    email_id = parser._get_id("external", "validate_email")
    uuid_id = parser._get_id("external", "validate_uuid")
    assert email_id in target_ids
    assert uuid_id in target_ids


def test_pytest_parametrize_keyword_argvalues():
    parser = PythonParser()
    code = b"""
from app.validators import validate_email

@pytest.mark.parametrize("validator", argvalues=[validate_email])
def test_kw_validator(validator):
    assert validator("sample") is True
"""
    nodes, edges = parser.extract("tests/test_kw.py", code)
    test_node = next(n for n in nodes if n.name == "test_kw_validator")

    test_call_edges = [
        e for e in edges if e.from_node_id == test_node.id and e.relation_type == "calls"
    ]
    target_ids = {e.to_node_id for e in test_call_edges}
    assert parser._get_id("external", "validate_email") in target_ids


def test_pytest_fixture_dependency_injection():
    parser = PythonParser()
    code = b"""
from db import init_database

@pytest.fixture
def db_session():
    return init_database()

def test_user_flow(db_session):
    assert db_session is not None
"""
    nodes, edges = parser.extract("tests/test_db.py", code)
    test_node = next(n for n in nodes if n.name == "test_user_flow")
    fixture_node = next(n for n in nodes if n.name == "db_session")

    # Verify test_user_flow calls db_session fixture
    test_calls = [e for e in edges if e.from_node_id == test_node.id and e.relation_type == "calls"]
    assert any(e.to_node_id == fixture_node.id for e in test_calls)

    # Verify db_session calls init_database
    fix_calls = [
        e for e in edges if e.from_node_id == fixture_node.id and e.relation_type == "calls"
    ]
    assert parser._get_id("external", "init_database") in {e.to_node_id for e in fix_calls}


def test_pytest_fixture_chain():
    parser = PythonParser()
    code = b"""
def load_config():
    return {}

@pytest.fixture
def base_config():
    return load_config()

@pytest.fixture
def client(base_config):
    return create_client(base_config)

def test_service(client):
    pass
"""
    nodes, edges = parser.extract("tests/test_chain.py", code)
    test_node = next(n for n in nodes if n.name == "test_service")
    client_node = next(n for n in nodes if n.name == "client")
    base_node = next(n for n in nodes if n.name == "base_config")

    # test_service -> client
    assert any(e.from_node_id == test_node.id and e.to_node_id == client_node.id for e in edges)
    # client -> base_config
    assert any(e.from_node_id == client_node.id and e.to_node_id == base_node.id for e in edges)


def test_js_test_each_table():
    parser = JavaScriptParser()
    code = b"""
import { validateEmail, validateUuid } from './validators';

test.each([
  validateEmail,
  validateUuid,
])('validates input', (fn) => {
  expect(fn('sample')).toBe(true);
});
"""
    _nodes, edges = parser.extract("tests/test_val.test.js", code)
    call_edges = [e for e in edges if e.relation_type == "calls"]
    target_ids = {e.to_node_id for e in call_edges}

    email_id = parser._get_id("external", "validateEmail")
    uuid_id = parser._get_id("external", "validateUuid")
    assert email_id in target_ids
    assert uuid_id in target_ids


def test_predictive_test_selection_with_dynamic_fixtures(tmp_path: Path):
    db_file = tmp_path / "test_index.db"
    db = IndexerDB(str(db_file))

    py_parser = PythonParser()
    test_code = b"""
from app.service import create_db, check_health

@pytest.fixture
def db_conn():
    return create_db()

@pytest.mark.parametrize("checker", [check_health])
def test_health_check(checker):
    assert checker() == "ok"

def test_db_queries(db_conn):
    assert db_conn is not None
"""
    nodes, edges = py_parser.extract("tests/test_integration.py", test_code)

    target_create_db = py_parser._get_id("external", "create_db")
    target_check_health = py_parser._get_id("external", "check_health")

    with db._get_conn() as conn:
        insert_node_sql = (
            "INSERT INTO nodes "
            "(id, name, qualified_name, kind, file_path, start_line, end_line) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)"
        )
        for n in nodes:
            conn.execute(
                insert_node_sql,
                (
                    n.id,
                    n.name,
                    n.qualified_name or n.name,
                    n.kind,
                    n.file_path,
                    n.start_line,
                    n.end_line,
                ),
            )
        # Add target symbols in app/service.py using the exact resolved IDs
        conn.execute(
            insert_node_sql,
            (
                target_create_db,
                "create_db",
                "service.create_db",
                "function",
                "app/service.py",
                1,
                10,
            ),
        )
        conn.execute(
            insert_node_sql,
            (
                target_check_health,
                "check_health",
                "service.check_health",
                "function",
                "app/service.py",
                11,
                20,
            ),
        )

        for e in edges:
            conn.execute(
                "INSERT INTO edges (from_node_id, to_node_id, relation_type, call_line) "
                "VALUES (?, ?, ?, ?)",
                (e.from_node_id, e.to_node_id, e.relation_type, e.call_line or 1),
            )

    # 1. Modifying check_health should impact test_health_check (via parametrize)
    report1 = PredictiveTestService.resolve_impacted_tests(
        db=db,
        project_root=tmp_path,
        target_symbols=["check_health"],
    )
    assert report1.total_impacted == 1
    assert report1.impacted_tests[0].test_function == "test_health_check"
    assert report1.impacted_tests[0].hops == 1

    # 2. Modifying create_db should impact test_db_queries (via db_conn fixture in 2 hops)
    report2 = PredictiveTestService.resolve_impacted_tests(
        db=db,
        project_root=tmp_path,
        target_symbols=["create_db"],
    )
    assert report2.total_impacted == 1
    assert report2.impacted_tests[0].test_function == "test_db_queries"
    assert report2.impacted_tests[0].hops == 2
