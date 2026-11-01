"""Tests for Adaptive Context Sliding Window for Multi-Turn Agent Chats."""

from pathlib import Path

from vibetrace.indexer.db import IndexerDB
from vibetrace.services.vibe_bundle import (
    AdaptiveSessionManager,
    AdaptiveSessionState,
)


def test_session_lifecycle(tmp_path: Path):
    session_id = "test-agent-sess-42"

    # Initially does not exist
    state = AdaptiveSessionManager.load_session(tmp_path, session_id)
    assert state is None

    # Turn 1
    state1 = AdaptiveSessionState(
        session_id=session_id,
        root_symbol="start_endpoint",
        current_focus="start_endpoint",
        active_path=["start_endpoint"],
        history_stack=["start_endpoint"],
        pinned_symbols=["start_endpoint"],
        token_budget=1500,
        turn_count=1,
    )
    AdaptiveSessionManager.save_session(tmp_path, state1)

    loaded = AdaptiveSessionManager.load_session(tmp_path, session_id)
    assert loaded is not None
    assert loaded.session_id == session_id
    assert loaded.turn_count == 1
    assert loaded.root_symbol == "start_endpoint"

    # Reset
    ok = AdaptiveSessionManager.reset_session(tmp_path, session_id)
    assert ok is True
    assert AdaptiveSessionManager.load_session(tmp_path, session_id) is None


def test_process_session_turns_and_pruning(tmp_path: Path):
    db_file = tmp_path / "index.db"
    db = IndexerDB(str(db_file))

    session_id = "agent-flow-99"

    # Turn 1: start at root
    res1 = AdaptiveSessionManager.process_session_turn(
        db=db,
        root=tmp_path,
        session_id=session_id,
        intent="Audit API",
        focus_symbol="auth_handler",
        token_budget=1200,
    )
    assert res1.session.turn_count == 1
    assert res1.session.root_symbol == "auth_handler"
    assert res1.session.current_focus == "auth_handler"
    assert "auth_handler" in res1.bundle.formatted_prompt

    # Turn 2: shift focus deeper down stack
    res2 = AdaptiveSessionManager.process_session_turn(
        db=db,
        root=tmp_path,
        session_id=session_id,
        intent="Check token verification",
        focus_symbol="verify_jwt",
        token_budget=1200,
    )
    assert res2.session.turn_count == 2
    assert res2.session.root_symbol == "auth_handler"
    assert res2.session.current_focus == "verify_jwt"
    assert res2.session.active_path == ["auth_handler", "verify_jwt"]
    assert "auth_handler -> verify_jwt" in res2.bundle.formatted_prompt

    # Turn 3: shift focus further down stack to crypto verification
    res3 = AdaptiveSessionManager.process_session_turn(
        db=db,
        root=tmp_path,
        session_id=session_id,
        intent="Validate crypto signature",
        focus_symbol="crypto_rsa_verify",
        token_budget=1200,
    )
    assert res3.session.turn_count == 3
    assert res3.session.root_symbol == "auth_handler"
    assert res3.session.current_focus == "crypto_rsa_verify"
    assert res3.session.active_path == ["auth_handler", "verify_jwt", "crypto_rsa_verify"]
