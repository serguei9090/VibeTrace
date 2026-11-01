"""Tests for Symbolic Inter-Procedural Alias and Dictionary Taint Tracking."""

from pathlib import Path

from vibetrace.security.contracts import (
    FunctionTaintContract,
    TaintContractManager,
)
from vibetrace.security.dataflow import (
    _propagate_function_def_use,
    verify_sink_taint_flow,
)


def test_contract_extracts_key_taint_and_aliases():
    source = """
def extract_credentials(payload):
    user = payload["username"]
    alias_user = user
    return alias_user
"""
    contracts = TaintContractManager.analyze_source_code(source, "auth.py")
    assert "extract_credentials" in contracts
    contract = contracts["extract_credentials"]
    assert isinstance(contract, FunctionTaintContract)
    assert 0 in contract.param_taints_return
    assert contract.param_taints_return[0] is True
    assert "payload" in contract.key_taints_return
    assert "username" in contract.key_taints_return["payload"]
    assert "alias_user" in contract.dict_aliases
    assert contract.dict_aliases["alias_user"] == "user"


def test_dictionary_subscript_taint_flow(tmp_path: Path):
    code = """
def handle_request(request):
    params = request.args
    user_query = params["search"]
    alias_filter = user_query
    return db.execute(f"SELECT * FROM items WHERE name = {alias_filter}")
"""
    f = tmp_path / "app.py"
    f.write_text(code, encoding="utf-8")

    is_tainted, lineage = verify_sink_taint_flow(
        project_root=tmp_path,
        file_path="app.py",
        func_start_line=2,
        func_end_line=7,
        sink_line=6,
        sanitizers=frozenset({"quote", "escape"}),
    )
    assert is_tainted is True
    assert any("params[search]" in hop for hop in lineage)
    assert any("alias_filter" in hop for hop in lineage)


def test_safe_dictionary_key_is_not_tainted(tmp_path: Path):
    code = """
def paginate_items(request):
    params = request.args
    page_num = params.get("page", 1)
    return db.execute(f"SELECT * FROM items LIMIT 10 OFFSET {page_num}")
"""
    f = tmp_path / "app_safe.py"
    f.write_text(code, encoding="utf-8")

    is_tainted, _lineage = verify_sink_taint_flow(
        project_root=tmp_path,
        file_path="app_safe.py",
        func_start_line=2,
        func_end_line=6,
        sink_line=5,
        sanitizers=frozenset({"quote", "escape"}),
    )
    # page_num is recognized as a safe key and untainted
    assert is_tainted is False


def test_alias_chain_propagation():
    lines = [
        "def process_input(raw_input):",
        "    first_alias = raw_input",
        "    second_alias = first_alias",
        "    target = second_alias",
        "    return target",
    ]
    _params, tainted, _untainted = _propagate_function_def_use(
        lines=lines,
        s_idx=0,
        stop_idx=5,
        sanitizers=frozenset(),
        tainted_param_indices={0: ["raw_input"]},
    )
    assert "target" in tainted
    assert "second_alias" in tainted["target"]
    assert "first_alias" in tainted["target"]
