"""Amendment scenarios: semantic vetoes and deterministic enforcement boundaries."""
import json

from harbor import referee
from harbor.agents.conversation import run
from harbor.engine import events, state
from harbor.llm.scripted import ScriptedLLM
from harbor.tools import REGISTRY
from test_lifecycle_tools import begin, call, draft, finalize


def outcomes(ctx):
    return [r[0] for r in ctx.game.conn.execute("SELECT outcome FROM attempts ORDER BY rowid")]


def semantic_rejection(ctx, text, message="Anything to tell me?"):
    # These words demonstrate the boundary: no keyword catches the invented fact.
    assert referee.code_check(ctx.game, "mira", message, text).ok
    checker = ScriptedLLM(["LEAK", "OK"])
    out = run(ctx, ScriptedLLM([text, "Quiet morning."]), checker, "mira", message)
    assert out["reply"] == "Quiet morning." and not out.get("fallback")
    assert outcomes(ctx) == ["model_leak", "accepted"]
    assert len(checker.calls) == 2
    assert text not in [r["text"] for r in state.conversation(ctx.game, "mira")]
    refs = [json.loads(r[0]) for r in ctx.game.conn.execute("SELECT attrs FROM spans WHERE kind='referee'")]
    assert refs[0]["referee.code_check"] == "pass" and refs[0]["referee.verdict"] == "LEAK"


def test_indirect_reveal_without_exact_keywords_needs_semantic_veto(ctx):
    semantic_rejection(ctx, "The merchant paid for a metal-cutting implement with his catch.")


def test_paraphrase_that_avoids_keywords_needs_semantic_veto(ctx):
    semantic_rejection(ctx, "A boat went out with no lights and came back an hour later.")


def test_combining_harmless_facts_into_a_new_accusation_is_vetoed(ctx):
    semantic_rejection(ctx, "The shop sells equipment. The merchant has fish. He traded those for the implement used in the crime.")


def test_leading_question_about_another_character_does_not_grant_knowledge(ctx):
    question = "Did the merchant purchase something from the shop?"
    semantic_rejection(ctx, "Yes, he paid for the implement with his catch.", question)
    ids = begin(ctx)
    call(ctx, "npc_context", "npc", **ids)
    assert call(ctx, "reveal_clue", "npc", **ids, clue_id="C5") == {
        "ok": False, "error": "you do not know clue C5"}


def test_persona_text_that_resembles_a_clue_is_not_a_leak(ctx):
    call(ctx, "go", place="fish market")
    text = "My bad knee is acting up. Fresh fish?"
    assert referee.code_check(ctx.game, "tom", "How are you?", text).ok
    out = run(ctx, ScriptedLLM([text]), ScriptedLLM(["OK"]), "tom", "How are you?")
    assert out["reply"] == text and not out.get("fallback")
    assert out["new_clues"] == [] and outcomes(ctx) == ["accepted"]


def world(ctx):
    return {table: [tuple(row) for row in ctx.game.conn.execute(f"SELECT * FROM {table}")]
            for table in events.HASHED_TABLES if table not in ("interactions", "attempts")}


def test_rejected_reveal_and_trust_commit_neither_and_leave_world_unchanged(ctx):
    call(ctx, "look")
    call(ctx, "go", place="chandlery")
    ids = begin(ctx, "ysolde", "The cable was cut clean. Who bought cutters?")
    before = world(ctx)
    for attempt in range(1, 4):
        ids["attempt"] = attempt
        call(ctx, "npc_context", "npc", **ids)
        assert call(ctx, "update_trust", "npc", **ids, reason="related_clue_shown")["trust"] == 1
        assert call(ctx, "reveal_clue", "npc", **ids, clue_id="C5")["ok"]
        assert world(ctx) == before
        call(ctx, "submit_draft", "npc", **ids, text="Morning.")
        call(ctx, "submit_verdict", "referee", **ids, verdict="LEAK")
        out = finalize(ctx, ids)
        if attempt < 3:
            assert world(ctx) == before and out["status"] == "retry"
    assert out["fallback"] and out["new_clues"] == []
    assert state.trust_of(ctx.game, "ysolde") == 0 and state.found_clue_ids(ctx.game) == {"C1"}
    assert not any(e["type"] in ("trust_changed", "clue_confirmed") or
                   e["type"] == "clue_found" and e["payload"]["clue_id"] == "C5"
                   for e in events.log(ctx.game.conn, ctx.game.run_id))


def test_invalid_referee_answer_never_accepts(ctx):
    out = run(ctx, ScriptedLLM(["Morning."] * 3), ScriptedLLM(["OK, but..."] * 3), "mira", "Hello")
    assert out["fallback"] and outcomes(ctx) == ["invalid_verdict"] * 3
    assert "Morning." not in [r["text"] for r in state.conversation(ctx.game, "mira")]


def test_forged_ok_cannot_override_keyword_leak(ctx):
    ids = begin(ctx)
    for attempt in range(1, 4):
        ids["attempt"] = attempt
        draft(ctx, ids, "Tom bought bolt cutters from Ysolde.")
        assert call(ctx, "submit_verdict", "referee", **ids, verdict="OK")["recorded"] == "OK"
        out = finalize(ctx, ids)
    assert out["fallback"] and out["new_clues"] == []
    assert outcomes(ctx) == ["code_leak"] * 3


def test_npc_cannot_use_other_interactions_or_finalized_ids(ctx):
    ids = begin(ctx)
    draft(ctx, ids)
    call(ctx, "submit_verdict", "referee", **ids, verdict="OK")
    finalize(ctx, ids)
    old = dict(ids)
    ids = begin(ctx)
    call(ctx, "npc_context", "npc", **ids)
    before = events.state_hash(ctx.game.conn, ctx.game.run_id)
    for stale in (old, {"interaction_id": "another-run-interaction", "attempt": 1}):
        for name, args in (("recall_knowledge", {}), ("recall_conversation", {}),
                           ("reveal_clue", {"clue_id": "C3"}), ("update_trust", {"reason": "rude"}),
                           ("submit_draft", {"text": "hello"})):
            assert not call(ctx, name, "npc", **stale, **args)["ok"]
    assert events.state_hash(ctx.game.conn, ctx.game.run_id) == before


def test_unknown_clue_and_solution_cannot_be_revealed(ctx):
    ids = begin(ctx)
    call(ctx, "npc_context", "npc", **ids)
    before = events.state_hash(ctx.game.conn, ctx.game.run_id)
    for clue in ("C999", "case_solution"):
        result = call(ctx, "reveal_clue", "npc", **ids, clue_id=clue)
        assert result == {"ok": False, "error": f"unknown clue {clue!r}"}
        assert events.state_hash(ctx.game.conn, ctx.game.run_id) == before
    assert not REGISTRY.get("talk")
