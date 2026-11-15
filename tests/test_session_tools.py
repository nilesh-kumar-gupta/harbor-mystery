import shutil
import sqlite3
from pathlib import Path

import pytest

from harbor.engine import state
from harbor.tools import Context, REGISTRY


def call(ctx, name, **args):
    return REGISTRY.call(ctx, name, args, "gm")


@pytest.fixture
def persistent(tmp_path, monkeypatch):
    monkeypatch.setenv("HARBOR_HOME", str(tmp_path))
    ctx = Context()
    assert call(ctx, "new_game")["ok"]
    yield ctx
    ctx.game.conn.close()


def test_no_game_dispatch():
    ctx = Context()
    assert call(ctx, "game_status") == {"ok": True, "loaded": False}
    assert call(ctx, "look")["ok"] is False
    assert REGISTRY.call(ctx, "new_game", {}, "npc")["ok"] is False
    assert call(ctx, "new_game", seed=True)["ok"] is False


def test_case_listing(tmp_path, monkeypatch):
    monkeypatch.setenv("HARBOR_CASES", str(tmp_path))
    shutil.copy(Path(__file__).parents[1] / "cases/saltmere.json", tmp_path / "saltmere.json")
    result = call(Context(), "new_game", case="absent")
    assert result["available"] == ["saltmere"]
    assert not result["ok"]


def test_status_and_active_refusal(persistent):
    ctx = persistent
    old = ctx.game
    assert call(ctx, "game_status")["case_id"] == "saltmere"
    assert not call(ctx, "new_game")["ok"]
    assert ctx.game is old
    assert call(ctx, "player_turn", line="hello")["ok"]
    for _ in range(5):
        assert call(ctx, "game_status")["ok"]
    for _ in range(3):
        assert call(ctx, "look")["ok"]
    assert call(ctx, "look")["error"] == "game master tool limit"


@pytest.mark.parametrize("name,args", [("look", {}), ("go", {"place": "bad"}),
    ("hint", {}), ("accuse", {}), ("begin_talk", {})])
def test_action_caps_unchanged(persistent, name, args):
    call(persistent, "player_turn", line="hello")
    for _ in range(3):
        call(persistent, "go", place="bad")
    assert call(persistent, name, **args)["error"] == "game master tool limit"


def test_switch_has_no_old_guard(persistent):
    ctx = persistent
    call(ctx, "accuse", character="Tom", motive="test", clue_ids=[])
    old, tracer = ctx.game, ctx.tracer
    assert state.is_over(old)
    assert call(ctx, "game_status")["ok"]
    assert call(ctx, "new_game")["ok"]
    assert ctx.game is not old
    assert old.tool_depth == 0 and old.span_id is None
    with pytest.raises(sqlite3.ProgrammingError):
        old.conn.execute("SELECT 1")
    assert not tracer._stack and not tracer._pending
    old.conn.close()


def test_resume_does_not_reset_action_cap(persistent):
    ctx = persistent
    call(ctx, "player_turn", line="hello")
    for _ in range(3):
        assert call(ctx, "look")["ok"]
    old = ctx.game
    assert call(ctx, "resume_game", run_id=old.run_id)["ok"]
    assert not old.tracer._virtual
    assert ctx.counts[(old.run_id, "gm", 1)] == 3
    assert call(ctx, "player_turn", line="continue")["turn"] == 2
    for _ in range(3):
        assert call(ctx, "look")["ok"]
    assert call(ctx, "look")["error"] == "game master tool limit"
    assert call(ctx, "close_turn")["ok"]
    assert not old.tracer._virtual


@pytest.mark.parametrize("case_id", ["saltmere", "gaiety", "kestrel"])
def test_all_shipped_cases_start(tmp_path, monkeypatch, case_id):
    monkeypatch.setenv("HARBOR_HOME", str(tmp_path))
    ctx = Context(require_turn=True)
    assert call(ctx, "new_game", case=case_id)["ok"]
    assert call(ctx, "game_status")["case_id"] == case_id
    assert ctx.game.conn.execute("SELECT COUNT(*) FROM runs").fetchone()[0] == 1
    ctx.game.conn.close()


def test_required_turn_refuses_actions_but_preserves_session_exemptions(persistent):
    ctx = persistent
    ctx.require_turn = True
    before = state.read(ctx.game)["clock"]
    for _ in range(6):
        assert call(ctx, "look")["error"] == "start a player turn first"
    assert state.read(ctx.game)["clock"] == before
    assert call(ctx, "game_status")["ok"]
    assert call(ctx, "player_turn", line="hello")["ok"]
    for _ in range(3):
        assert call(ctx, "look")["ok"]
    assert call(ctx, "look")["error"] == "game master tool limit"
    assert call(ctx, "close_turn")["ok"]
    assert call(ctx, "look")["error"] == "start a player turn first"


def test_session_open_requires_turn(persistent):
    from harbor.session import Session
    from harbor.tools.session_tools import release_lease
    release_lease(persistent)
    session = Session.open(persistent.game.run_id)
    assert session.ctx.require_turn
    assert not call(session.ctx, "look")["ok"]
    session.ctx.game.conn.close()
