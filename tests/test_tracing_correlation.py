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


def test_unknown_virtual_close_counts_failure(ctx):
    before = ctx.tracer.trace_failures
    ctx.tracer.close_virtual("unknown")
    assert ctx.tracer.trace_failures == before + 1


def test_closed_virtual_cannot_reopen_and_parent_supplies_trace(ctx):
    import pytest
    t = ctx.tracer
    parent = t.open_virtual("agent", "parent", "parent", trace_id="original")
    t.begin_trace("different")
    with t.span("tool_call", "child", parent_id=parent.span_id) as child:
        assert child.trace_id == "original"
    virtual = t.open_virtual("agent", "child", "virtual", parent_id=parent.span_id)
    assert virtual.trace_id == "original"
    t.close_virtual(virtual.span_id)
    with pytest.raises(ValueError, match="already closed"):
        t.open_virtual("agent", "child", "virtual")
    t.close_virtual(parent.span_id)
    with t.span("tool_call", "later", parent_id=parent.span_id) as child:
        assert child.trace_id == "original"


def test_finalize_pending_and_after_tracer_restart_commit(ctx):
    from harbor.tracing import Tracer
    from test_lifecycle_tools import begin, draft, finalize, call
    ids = begin(ctx)
    assert finalize(ctx, ids)["reason_code"] == "no_draft"
    ids["attempt"] = 2
    draft(ctx, ids)
    call(ctx, "submit_verdict", "referee", **ids, verdict="OK")
    ctx.tracer = Tracer(ctx.game.conn, ctx.game.run_id)
    assert finalize(ctx, ids)["outcome"] == "accepted"
