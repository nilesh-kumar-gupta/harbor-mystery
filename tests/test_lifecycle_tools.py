"""Public lifecycle contracts, process caps, and cross-handoff durability."""
import json

import pytest

from harbor.engine import events, state
from harbor.tools import REGISTRY
from harbor.llm.scripted import ScriptedLLM
from harbor.agents.conversation import run


def call(ctx, name, audience="gm", **args):
    return REGISTRY.call(ctx, name, args, audience)


def begin(ctx, character="mira", message="last night on the quay?"):
    out = call(ctx, "begin_talk", character=character, message=message)
    assert out["ok"], out
    return {"interaction_id": out["interaction_id"], "attempt": 1}


def draft(ctx, ids, text="Morning."):
    assert call(ctx, "npc_context", "npc", **ids)["ok"]
    assert call(ctx, "submit_draft", "npc", **ids, text=text)["ok"]


def finalize(ctx, ids):
    return call(ctx, "finalize_talk", interaction_id=ids["interaction_id"])


def test_full_lifecycle_is_durable_and_duplicate_finalize_writes_nothing(ctx, case):
    ids = begin(ctx)
    assert not ctx.game.conn.in_transaction
    draft(ctx, ids)
    assert call(ctx, "referee_context", "referee", **ids)["reply"] == "Morning."
    assert call(ctx, "submit_verdict", "referee", **ids, verdict="OK")["recorded"] == "OK"
    out = finalize(ctx, ids)
    assert out["outcome"] == "accepted"
    before = events.log(ctx.game.conn, ctx.game.run_id)
    assert finalize(ctx, ids) == {**out, "already_finalized": True}
    assert events.log(ctx.game.conn, ctx.game.run_id) == before
    fresh = state.new_game(case, "replayed")
    events.replay(fresh.conn, fresh.run_id, before)
    assert events.state_hash(fresh.conn, fresh.run_id) == events.state_hash(ctx.game.conn, ctx.game.run_id)
    assert not ctx.game.conn.in_transaction


def test_wrong_phase_attempt_unknown_and_finalized_ids(ctx):
    ids = begin(ctx)
    assert call(ctx, "recall_knowledge", "npc", **ids)["error"] == "wrong interaction state"
    assert call(ctx, "submit_verdict", "referee", **ids, verdict="OK")["error"] == "wrong interaction state"
    assert call(ctx, "npc_context", "npc", **{**ids, "attempt": 2})["error"] == "wrong attempt"
    assert call(ctx, "npc_context", "npc", interaction_id="absent", attempt=1)["error"] == "unknown interaction"
    draft(ctx, ids)
    assert call(ctx, "recall_knowledge", "npc", **ids)["error"] == "wrong interaction state"
    call(ctx, "submit_verdict", "referee", **ids, verdict="OK")
    finalize(ctx, ids)
    assert call(ctx, "npc_context", "npc", **ids)["error"] == "stale interaction"


def test_duplicate_and_conflicting_draft_and_verdict(ctx):
    ids = begin(ctx)
    draft(ctx, ids)
    before = events.log(ctx.game.conn, ctx.game.run_id)
    assert call(ctx, "submit_draft", "npc", **ids, text="Morning.")["ok"]
    assert not call(ctx, "submit_draft", "npc", **ids, text="Different.")["ok"]
    assert events.log(ctx.game.conn, ctx.game.run_id) == before
    assert call(ctx, "submit_verdict", "referee", **ids, verdict=" OK ")["recorded"] == "OK"
    before = events.log(ctx.game.conn, ctx.game.run_id)
    assert call(ctx, "submit_verdict", "referee", **ids, verdict="OK")["ok"]
    assert not call(ctx, "submit_verdict", "referee", **ids, verdict="LEAK")["ok"]
    assert events.log(ctx.game.conn, ctx.game.run_id) == before


def test_fifth_call_flags_attempt_even_if_first_four_failed(ctx):
    ids = begin(ctx)
    call(ctx, "npc_context", "npc", **ids)
    for _ in range(4):
        assert not call(ctx, "reveal_clue", "npc", **ids, clue_id="missing")["ok"]
    assert call(ctx, "recall_knowledge", "npc", **ids)["error"] == "NPC tool limit"
    call(ctx, "submit_draft", "npc", **ids, text="Morning.")
    call(ctx, "submit_verdict", "referee", **ids, verdict="OK")
    assert finalize(ctx, ids)["reason_code"] == "tool_limit"
    assert call(ctx, "npc_context", "npc", **{**ids, "attempt": 2})["calls_left"] == 4


def test_staged_trust_visible_locally_and_dropped_on_retry(ctx):
    call(ctx, "look")
    call(ctx, "go", place="chandlery")
    ids = begin(ctx, "ysolde", "The cable was cut clean. Who bought cutters?")
    call(ctx, "npc_context", "npc", **ids)
    assert call(ctx, "update_trust", "npc", **ids, reason="related_clue_shown")["trust"] == 1
    assert call(ctx, "recall_knowledge", "npc", **ids)["trust"] == 1
    assert call(ctx, "reveal_clue", "npc", **ids, clue_id="C5")["staged"]
    assert state.trust_of(ctx.game, "ysolde") == 0
    call(ctx, "submit_draft", "npc", **ids, text="Morning.")
    call(ctx, "submit_verdict", "referee", **ids, verdict="LEAK")
    assert finalize(ctx, ids)["reason_code"] == "model_leak"
    ids["attempt"] = 2
    call(ctx, "npc_context", "npc", **ids)
    assert call(ctx, "recall_knowledge", "npc", **ids)["trust"] == 0
    assert state.found_clue_ids(ctx.game) == {"C1"}


@pytest.mark.parametrize("name,args", [("look", {}), ("go", {"place": "office"}), ("hint", {}),
    ("accuse", {"character": "tom", "motive": "x", "clue_ids": []}),
    ("begin_talk", {"character": "mira", "message": "hello"}), ("close_turn", {}),
    ("player_turn", {"line": "hello"})])
def test_gm_tools_blocked_during_open_interaction(ctx, name, args):
    ids = begin(ctx)
    assert call(ctx, name, **args) == {"ok": False, "error": "finish the conversation first",
                                     "interaction_id": ids["interaction_id"]}
    assert call(ctx, "notebook")["ok"]


def test_turn_cap_failed_actions_and_automatic_close(ctx):
    assert call(ctx, "player_turn", line="go")["ok"]
    for _ in range(3):
        assert call(ctx, "go", place="moon")["error"].startswith("unknown place")
    assert call(ctx, "look")["error"] == "game master tool limit"
    assert call(ctx, "notebook")["ok"]
    out = call(ctx, "player_turn", line="look")
    assert out["turn"] == 2 and out["previous"]["turn"] == 2
    assert call(ctx, "look")["ok"]
    assert call(ctx, "close_turn")["ok"]


def test_no_savepoint_crosses_model_handoffs_and_trace_reconstructs_retry(ctx):
    def npc(system, messages, tools):
        assert not ctx.game.conn.in_transaction and ctx.game.tool_depth == 0
        return "Morning."
    answers = iter(["OK but unsure", "OK"])
    def checker(*args):
        assert not ctx.game.conn.in_transaction and ctx.game.tool_depth == 0
        return next(answers)
    call(ctx, "player_turn", line="hello")
    result = run(ctx, ScriptedLLM(npc), ScriptedLLM(checker), "mira", "hello")
    assert result["reply"] == "Morning."
    call(ctx, "close_turn")
    rows = [dict(r) for r in ctx.game.conn.execute("SELECT * FROM spans")]
    refs = [r for r in rows if r["kind"] == "referee"]
    assert [json.loads(r["attrs"])["referee.verdict"] for r in refs] == ["INVALID", "OK"]
    finals = [r for r in rows if r["name"] == "finalize_talk"]
    assert json.loads(finals[0]["output"])["reason_code"] == "invalid_verdict"
    assert json.loads(finals[1]["output"])["reply"] == "Morning."
    iid = json.loads(finals[0]["attrs"])["game.interaction_id"]
    by_id = {r["span_id"]: r for r in rows}
    for attempt in (1, 2):
        npc_span = by_id[f"{iid}-a{attempt}-npc"]
        ref_span = by_id[f"{iid}-a{attempt}-ref"]
        assert npc_span["parent_span_id"] == ref_span["parent_span_id"] == "test-run-t0001-gm"
        assert json.loads(npc_span["output"])["text"] == "Morning."
        for name in ("npc_context", "submit_draft", "referee_context", "submit_verdict"):
            selected = [r for r in rows if r["name"] == name and json.loads(r["attrs"]).get("game.attempt") == attempt]
            assert selected
            assert all(r["parent_span_id"] == (npc_span if name in ("npc_context", "submit_draft") else ref_span)["span_id"] for r in selected)
    attrs = json.loads(finals[1]["attrs"])
    committed = [e for e in events.log(ctx.game.conn, ctx.game.run_id) if e["span_id"] == finals[1]["span_id"]]
    assert [e["seq"] for e in committed] == list(range(attrs["game.event_seq_start"], attrs["game.event_seq_end"] + 1))
    assert any("could not be checked" in str(r["input"]) for r in rows if r["kind"] == "llm_call")


@pytest.mark.parametrize("answer,reason,expected,error", [
    (None, None, "missing", False), ("OK but", "", "INVALID", False),
    (None, "model_error: RuntimeError", "INVALID", True)])
def test_missing_invalid_and_model_error_remain_distinct(ctx, answer, reason, expected, error):
    ids = begin(ctx)
    draft(ctx, ids)
    if reason is not None:
        call(ctx, "submit_verdict", "referee", **ids, verdict=answer, reason=reason)
    result = finalize(ctx, ids)
    assert result["reason_code"] == ("no_verdict" if expected == "missing" else "invalid_verdict")
    row = ctx.game.conn.execute("SELECT * FROM spans WHERE kind='referee'").fetchone()
    attrs = json.loads(row["attrs"])
    assert attrs["referee.verdict"] == expected and attrs["referee.model_error"] is error
    assert row["status"] == ("error" if error else "rejected")


def test_failed_and_empty_drafts_retry_three_times_then_fallback(ctx):
    out = run(ctx, ScriptedLLM(["", "", ""]), None, "mira", "hello")
    assert out["fallback"]
    assert [r[0] for r in ctx.game.conn.execute("SELECT outcome FROM attempts ORDER BY attempt")] == ["no_draft"] * 3
    assert len(state.conversation(ctx.game, "mira")) == 2


def test_malformed_new_turn_does_not_close_previous_turn(ctx):
    call(ctx, "player_turn", line="hello")
    before = events.log(ctx.game.conn, ctx.game.run_id)
    assert not call(ctx, "player_turn", line=17)["ok"]
    assert ctx.turn_open and state.read(ctx.game)["turn"] == 1
    assert events.log(ctx.game.conn, ctx.game.run_id) == before


def test_malformed_and_unknown_npc_calls_cannot_evade_attempt_cap(ctx):
    ids = begin(ctx)
    call(ctx, "npc_context", "npc", **ids)
    for name, args in (("reveal_clue", {}), ("update_trust", {"reason": "made_up"}),
                       ("recall_knowledge", {"character": "tom"}), ("invent_fact", {})):
        assert not call(ctx, name, "npc", **ids, **args)["ok"]
    assert call(ctx, "recall_knowledge", "npc", **ids)["error"] == "NPC tool limit"
    assert finalize(ctx, ids)["reason_code"] == "tool_limit"


def test_unknown_gm_actions_count_towards_cap(ctx):
    call(ctx, "player_turn", line="hello")
    for _ in range(3):
        assert not call(ctx, "invent_fact")["ok"]
    assert call(ctx, "look")["error"] == "game master tool limit"


def test_virtual_talk_returns_schema_errors_as_values(ctx):
    from harbor.agents.game_master import GameMaster
    gm = GameMaster(ScriptedLLM([]), ScriptedLLM([]))
    before = events.log(ctx.game.conn, ctx.game.run_id)
    assert gm.execute(ctx, "talk", {"character": "mira"})["error"] == "missing argument 'message'"
    assert gm.execute(ctx, "talk", {"character": "mira", "message": 3})["error"] == "argument 'message' must be of type string"
    assert events.log(ctx.game.conn, ctx.game.run_id) == before


def test_finalize_always_supplies_code_check_and_leak_note(ctx, monkeypatch):
    from harbor import referee
    from harbor.engine import interaction
    original = interaction.finalize
    checks = []
    def inspected(game, iid, check, **kwargs):
        checks.append(check)
        assert check is referee.code_check
        return original(game, iid, check, **kwargs)
    monkeypatch.setattr(interaction, "finalize", inspected)
    assert not call(ctx, "finalize_talk", interaction_id="absent")["ok"]
    ids = begin(ctx, message="hello")
    draft(ctx, ids, "bolt cutters")
    call(ctx, "submit_verdict", "referee", **ids, verdict="OK")
    assert finalize(ctx, ids)["reason_code"] == "code_leak"
    ids["attempt"] = 2
    assert call(ctx, "npc_context", "npc", **ids)["note"] == referee.LEAK_NOTE
    draft(ctx, ids)
    call(ctx, "submit_verdict", "referee", **ids, verdict="OK")
    assert finalize(ctx, ids)["outcome"] == "accepted"
    assert finalize(ctx, ids)["already_finalized"]
    assert len(checks) == 4
