"""Tracing: the span() helper, the JSONL mirror, and the rule that state only
changes inside a tool_call span."""

import json

import pytest

from harbor.engine import events, state
from harbor.tools import REGISTRY, Context
from harbor.tracing import COLUMNS, Tracer


def spans(conn):
    return [dict(r) for r in conn.execute("SELECT * FROM spans ORDER BY rowid")]


@pytest.fixture
def traced(case, tmp_path):
    """A started run whose tracer also writes a JSONL file."""
    game = state.new_game(case, "trace-run", seed=1)
    context = Context(game=game, tracer=Tracer(game.conn, "trace-run", tmp_path / "t.jsonl"))
    REGISTRY.call(context, "start_run", {}, "engine")
    return context


def test_nested_spans_record_parents_and_fields(traced):
    tracer = traced.tracer
    tracer.begin_trace("t0001")
    with tracer.span("agent", "game_master", input="look around",
                     attrs={"gen_ai.agent.name": "game_master"}) as agent:
        with tracer.span("llm_call", "chat", input={"messages": 1}) as llm:
            llm.model, llm.input_tokens, llm.output_tokens = "scripted", 12, 3
            llm.output = {"text": "hi"}
        with tracer.span("referee", "referee", parent=agent) as ref:
            ref.status = "rejected"
    rows = {r["name"]: r for r in spans(traced.game.conn) if r["trace_id"] == "trace-run-t0001"}
    assert rows["game_master"]["parent_span_id"] is None
    assert rows["chat"]["parent_span_id"] == rows["game_master"]["span_id"]
    assert rows["referee"]["parent_span_id"] == rows["game_master"]["span_id"]
    assert (rows["chat"]["model"], rows["chat"]["input_tokens"], rows["chat"]["output_tokens"]) \
        == ("scripted", 12, 3)
    assert json.loads(rows["chat"]["output"]) == {"text": "hi"}
    assert rows["referee"]["status"] == "rejected"
    assert json.loads(rows["game_master"]["attrs"]) == {"gen_ai.agent.name": "game_master"}
    assert rows["chat"]["start_ts"] <= rows["chat"]["end_ts"]


def test_an_exception_marks_the_span_and_still_writes_it(traced):
    with pytest.raises(KeyError):
        with traced.tracer.span("agent", "boom"):
            raise KeyError("x")
    row = [r for r in spans(traced.game.conn) if r["name"] == "boom"][0]
    assert row["status"] == "error" and json.loads(row["attrs"])["error.type"] == "KeyError"
    assert traced.tracer.current() is None
    with pytest.raises(ValueError):
        with traced.tracer.span("mystery", "x"):
            pass


def test_jsonl_mirrors_the_spans_table(traced, tmp_path):
    REGISTRY.call(traced, "look", {}, "gm")
    REGISTRY.call(traced, "go", {"place": "moon"}, "gm")
    lines = [json.loads(line) for line in (tmp_path / "t.jsonl").read_text().splitlines()]
    rows = spans(traced.game.conn)
    assert [l["span_id"] for l in lines] == [r["span_id"] for r in rows]
    assert set(lines[0]) == set(COLUMNS)
    assert [(l["name"], l["status"]) for l in lines] == [
        ("start_run", "ok"), ("look", "ok"), ("go", "error")]
    assert lines[2]["input"] == {"place": "moon"} and lines[2]["output"]["ok"] is False


def test_every_tool_call_is_a_span_with_genai_attributes(traced):
    traced.begin_turn()
    REGISTRY.call(traced, "look", {}, "gm")
    REGISTRY.call(traced, "fly", {}, "gm")             # unknown tools are traced too
    REGISTRY.call(traced, "go", {}, "gm")              # and schema failures
    REGISTRY.call(traced, "end_turn", {}, "engine")
    rows = [r for r in spans(traced.game.conn) if r["trace_id"] == "trace-run-t0001"]
    assert [(r["kind"], r["name"], r["status"]) for r in rows] == [
        ("tool_call", "look", "ok"), ("tool_call", "fly", "error"),
        ("tool_call", "go", "error"), ("tool_call", "end_turn", "ok")]
    attrs = json.loads(rows[0]["attrs"])
    assert attrs["gen_ai.operation.name"] == "execute_tool"
    assert attrs["gen_ai.tool.name"] == "look" and attrs["game.turn"] == 1
    assert json.loads(rows[0]["output"])["place"] == "Harbor Quay"
    assert traced.begin_turn() == 2 and traced.tracer.trace_id == "trace-run-t0002"


def assert_state_changes_only_inside_tool_calls(conn, run_id):
    """Every event must carry the id of a tool_call span that was open when it was written."""
    kinds = {r["span_id"]: r["kind"] for r in conn.execute("SELECT span_id, kind FROM spans")}
    log = events.log(conn, run_id)
    assert log, "no events to check"
    for e in log:
        assert kinds.get(e["span_id"]) == "tool_call", e


def test_no_state_change_outside_a_tool_call_span(traced):
    for name, arguments in (("look", {}), ("go", {"place": "office"}), ("go", {"place": "sea"}),
                            ("notebook", {})):
        REGISTRY.call(traced, name, arguments, "gm")
    REGISTRY.call(traced, "end_turn", {}, "engine")
    g = traced.game
    assert_state_changes_only_inside_tool_calls(g.conn, g.run_id)
    by_span = {}
    for e in events.log(g.conn, g.run_id):
        by_span.setdefault(e["span_id"], []).append(e["type"])
    names = {r["span_id"]: r["name"] for r in spans(g.conn)}
    assert {names[s]: types for s, types in by_span.items()} == {
        "start_run": ["run_started"], "look": ["clock_advanced", "clue_found"],
        "go": ["player_moved", "clock_advanced"], "end_turn": ["turn_ended"]}
    assert g.span_id is None and g.tool_depth == 0


def test_a_context_always_has_a_tracer(ctx):
    assert isinstance(ctx.tracer, Tracer)
    assert [r["name"] for r in spans(ctx.game.conn)] == ["start_run"]


def test_spans_inside_a_rolled_back_tool_call_survive(traced, monkeypatch):
    """A failing tool is rolled back, but what was traced inside it is kept."""
    from harbor.engine import state as st

    def failing_look(ctx):
        st.advance_clock(ctx.game, 5)
        with ctx.tracer.span("llm_call", "inner") as inner:
            inner.status = "rejected"
        return {"ok": False, "error": "nope"}
    tool = REGISTRY.get("look")
    monkeypatch.setitem(REGISTRY._tools, "look", type(tool)(
        tool.name, tool.audience, tool.description, tool.input_schema, failing_look))
    before = events.state_hash(traced.game.conn, traced.game.run_id)
    assert REGISTRY.call(traced, "look", {}, "gm") == {"ok": False, "error": "nope"}
    assert events.state_hash(traced.game.conn, traced.game.run_id) == before
    rows = {r["name"]: r for r in spans(traced.game.conn)}
    assert rows["inner"]["status"] == "rejected"
    assert rows["inner"]["parent_span_id"] == rows["look"]["span_id"]
    assert rows["look"]["status"] == "error"


def test_two_contexts_on_one_game_share_a_tracer(traced):
    other = Context(game=traced.game)
    assert other.tracer is traced.tracer
    assert REGISTRY.call(other, "look", {}, "gm")["ok"]
    ids = [r["span_id"] for r in spans(traced.game.conn)]
    assert len(ids) == len(set(ids))


def test_a_new_tracer_on_an_existing_database_continues_numbering(traced):
    fresh = Tracer(traced.game.conn, traced.game.run_id)
    with fresh.span("agent", "later"):
        pass
    ids = [r["span_id"] for r in spans(traced.game.conn)]
    assert len(ids) == len(set(ids)) and ids[-1].endswith("s000002")


def test_a_trace_write_failure_never_reaches_the_tool_caller(case, tmp_path, capsys):
    game = state.new_game(case, "bad-jsonl")
    (tmp_path / "dir.jsonl").mkdir()
    context = Context(game=game, tracer=Tracer(game.conn, "bad-jsonl", tmp_path / "dir.jsonl"))
    assert REGISTRY.call(context, "start_run", {}, "engine")["ok"]
    assert "could not write span" in capsys.readouterr().err


def test_a_trace_cannot_change_under_an_open_span(traced):
    with traced.tracer.span("agent", "game_master"):
        with pytest.raises(RuntimeError):
            traced.tracer.begin_trace("t0009")
    with traced.tracer.span("agent", "a", parent=None) as a:
        pass
    traced.tracer.begin_trace("t0001")
    with traced.tracer.span("llm_call", "child", parent=a):
        pass
    rows = {r["name"]: r for r in spans(traced.game.conn)}
    assert rows["child"]["trace_id"] == rows["a"]["trace_id"]   # children stay in their parent's trace


def test_an_interrupt_inside_a_tool_rolls_back_and_closes_the_savepoint(traced, monkeypatch):
    before = events.state_hash(traced.game.conn, traced.game.run_id)
    def interrupted(*a):
        raise KeyboardInterrupt
    monkeypatch.setattr(state, "advance_clock", interrupted)
    with pytest.raises(KeyboardInterrupt):
        REGISTRY.call(traced, "go", {"place": "office"}, "gm")
    assert not traced.game.conn.in_transaction
    assert events.state_hash(traced.game.conn, traced.game.run_id) == before


TWENTY_TURNS = [
    "look", "talk mira: did you see anyone on the quay last night?", "talk mira: and the tavern?",
    "go office", "talk odrin: tell me about the town meeting", "talk odrin: who wished the harbor ill?",
    "go quay", "go chandlery", "talk ysolde: the cable was cut clean. who bought cutters?",
    "go lighthouse", "talk bram: any boats on the water last night?", "go chandlery", "go quay",
    "go fish market", "talk tom: where were you last night?", "go salt cask",
    "talk hale: was Tom here at midnight?", "notebook", "hint", "talk hale: thank you",
]


def test_a_twenty_turn_game_is_fully_traced(make_session):
    """The brief's Trace check: every turn has agent, tool_call and llm_call spans
    with parents, every talk has its NPC agent and referee spans, and no state
    changes outside a tool_call span of the same turn."""
    session = make_session()
    talks = []
    for line in TWENTY_TURNS:
        result = session.turn(line)
        talks.append(any(name == "talk" for name, _, _ in result.tool_results))
    conn = session.ctx.game.conn
    rows = [dict(r) for r in conn.execute("SELECT * FROM spans ORDER BY rowid")]
    by_id = {r["span_id"]: r for r in rows}
    checked = 0
    for r in rows:                               # every parent exists, in the same trace
        if r["parent_span_id"]:
            assert by_id[r["parent_span_id"]]["trace_id"] == r["trace_id"]
    for turn, has_talk in enumerate(talks, start=1):
        spans = [r for r in rows if r["trace_id"].endswith(f"-t{turn:04d}")]
        kinds = {r["kind"] for r in spans}
        assert {"agent", "tool_call", "llm_call"} <= kinds, turn
        roots = [r for r in spans if r["parent_span_id"] is None]
        assert [(r["kind"], r["name"]) for r in roots] == [("agent", "game_master")], turn
        if has_talk:
            npc = [r for r in spans if r["kind"] == "agent" and r["name"].startswith("npc:")]
            ref = [r for r in spans if r["kind"] == "referee"]
            assert npc, turn
            gm = [r for r in spans if r["name"] == "game_master"][0]
            assert all(by_id[r["parent_span_id"]] is gm for r in npc + ref)
            begin = [r for r in spans if r["name"] == "begin_talk"]
            finalize = [r for r in spans if r["name"] == "finalize_talk"]
            assert len(begin) == 1 and len(finalize) == len(ref)
            assert all(by_id[r["parent_span_id"]] is gm for r in begin + finalize)
            attempt = lambda r: json.loads(r["attrs"])["game.attempt"]
            drafts = [r for r in npc if "game.fallback" not in json.loads(r["attrs"])]
            # every draft with words is judged; an empty draft goes straight to the fallback
            assert sorted(map(attempt, drafts)) == sorted(map(attempt, ref)), turn
            if any(json.loads(r["output"] or "{}").get("text") for r in drafts):
                assert any(by_id[r["parent_span_id"]]["kind"] == "referee"
                           for r in spans if r["kind"] == "llm_call")   # the model check
                checked += 1
    assert sum(talks) >= 9 and checked >= 6
    for e in events.log(conn, session.ctx.game.run_id):
        span = by_id[e["span_id"]]
        assert span["kind"] == "tool_call"
        if e["type"] != "run_started":
            assert span["trace_id"].endswith(f"-t{e['turn']:04d}"), e
