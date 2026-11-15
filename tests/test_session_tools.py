import shutil
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
    assert not old.conn.in_transaction
    assert not tracer._stack and not tracer._pending
    old.conn.close()


def test_resume_does_not_reset_action_cap(persistent):
    ctx = persistent
    call(ctx, "player_turn", line="hello")
    for _ in range(3):
        assert call(ctx, "look")["ok"]
    old = ctx.game
    assert call(ctx, "resume_game", run_id=old.run_id)["ok"]
    assert call(ctx, "player_turn", line="continue")["ok"]
    assert call(ctx, "look")["error"] == "game master tool limit"
    assert not old.tracer._virtual
    old.conn.close()
