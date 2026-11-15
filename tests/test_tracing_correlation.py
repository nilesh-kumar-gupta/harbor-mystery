import json

from harbor.engine import state
from harbor.tools import REGISTRY, Context
from harbor.tracing import Tracer


def test_deferred_ids_parents_and_explicit_correlation(ctx):
    t = ctx.tracer
    trace = t.begin_trace("t0001")
    gm = t.open_virtual("agent", "game_master", "test-run-t0001-gm", trace_id=trace)
    npc = t.open_virtual("agent", "npc:mira", "i001-abcd-a1-npc", parent_id=gm.span_id,
                         trace_id=trace, attrs={"game.interaction_id": "i001-abcd", "game.attempt": 1})
    with t.span("tool_call", "submit_draft", parent_id=npc.span_id, trace_id=trace) as tool:
        tool.output = {"status": "evaluating"}
    assert not ctx.game.conn.in_transaction
    assert ctx.game.conn.execute("SELECT 1 FROM spans WHERE span_id=?", (gm.span_id,)).fetchone() is None
    t.close_virtual(npc.span_id, status="rejected")
    t.close_virtual(gm.span_id)
    rows = {r["span_id"]: dict(r) for r in ctx.game.conn.execute("SELECT * FROM spans")}
    assert rows[tool.span_id]["parent_span_id"] == npc.span_id
    assert rows[npc.span_id]["parent_span_id"] == gm.span_id
    assert rows[npc.span_id]["status"] == "rejected"
    assert all(rows[s]["trace_id"] == trace for s in (gm.span_id, npc.span_id, tool.span_id))
    assert json.loads(rows[npc.span_id]["attrs"])["game.attempt"] == 1


def test_failing_span_write_counts_and_keeps_committed_state(ctx, monkeypatch, capsys):
    def fail(*args):
        raise OSError("disk unavailable")
    monkeypatch.setattr(ctx.tracer, "_write", fail)
    assert REGISTRY.call(ctx, "look", {}, "gm")["ok"]
    assert state.read(ctx.game)["clock"] == 365
    assert ctx.tracer.trace_failures == 1
    assert "disk unavailable" in capsys.readouterr().err
