import json

import pytest

from harbor.engine import interaction, state
from harbor.session import Session
from harbor.tools import Context, REGISTRY


def call(ctx, name, **args):
    return REGISTRY.call(ctx, name, args, REGISTRY.get(name).audience)


@pytest.mark.parametrize("phase", ["pending", "drafting", "evaluating"])
def test_reopen_recovers_once(tmp_path, monkeypatch, phase):
    monkeypatch.setenv("HARBOR_HOME", str(tmp_path))
    ctx = Context()
    call(ctx, "new_game")
    run_id = ctx.game.run_id
    call(ctx, "player_turn", line="hello")
    begin = call(ctx, "begin_talk", character="Mira", message="hello")
    ids = {"interaction_id": begin["interaction_id"], "attempt": 1}
    if phase != "pending":
        call(ctx, "npc_context", **ids)
        call(ctx, "update_trust", **ids, reason="polite_repeat")
    if phase == "evaluating":
        call(ctx, "submit_draft", **ids, text="Hm.")
    before = state.read(ctx.game)["clock"]
    ctx.game.conn.close()
    session = Session.open(run_id)
    ctx = session.ctx
    row = interaction.get(ctx.game, ids["interaction_id"])
    assert row["outcome"] == "interrupted" and row["fallback"]
    assert state.read(ctx.game)["clock"] == before
    lines = ctx.game.conn.execute("SELECT * FROM conversations WHERE speaker='npc'").fetchall()
    assert len(lines) == 1
    assert lines[0]["text"] == ctx.game.conn.execute("SELECT fallback_line FROM characters WHERE id='mira'").fetchone()[0]
    spans = ctx.game.conn.execute("SELECT * FROM spans WHERE status='interrupted'").fetchall()
    assert len(spans) == 3
    assert all(json.loads(s["attrs"])["harbor.reconstructed"] for s in spans)
    assert not call(ctx, "submit_draft", **ids, text="late")["ok"]
    assert call(ctx, "finalize_talk", interaction_id=ids["interaction_id"])["already_finalized"]
    old = ctx.game
    result = call(ctx, "resume_game", run_id=run_id)
    assert result["recovered"] == []
    assert ctx.game.conn.execute("SELECT COUNT(*) FROM conversations WHERE speaker='npc'").fetchone()[0] == 1
    assert old.tool_depth == 0 and old.span_id is None and not old.conn.in_transaction
    old.conn.close()
    ctx.game.conn.close()


def test_resume_allowed_during_interaction(tmp_path, monkeypatch):
    monkeypatch.setenv("HARBOR_HOME", str(tmp_path))
    ctx = Context()
    call(ctx, "new_game")
    call(ctx, "player_turn", line="hello")
    call(ctx, "begin_talk", character="Mira", message="hello")
    assert call(ctx, "game_status")["open_interaction"]
    old = ctx.game
    result = call(ctx, "resume_game")
    assert result["ok"] and len(result["recovered"]) == 1
    assert not interaction.opened(ctx.game)
    old.conn.close()
    ctx.game.conn.close()
