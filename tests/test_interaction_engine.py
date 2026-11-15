"""Engine lifecycle exercised through a registry tool, including write guards."""
import json

import pytest

from harbor import referee
from harbor.engine import events, interaction, schema, state
from harbor.engine.case_loader import insert_case
from harbor.tools.registry import Registry


@pytest.fixture
def lifecycle(ctx):
    registry = Registry()

    @registry.tool("exercise", "engine", "Engine test")
    def exercise(context):
        return operation[0]()

    operation = [None]

    def run(fn):
        operation[0] = fn
        result = registry.call(ctx, "exercise", {}, "engine")
        assert not ctx.game.conn.in_transaction
        return result

    return ctx.game, run


def begin(game, run):
    out = run(lambda: interaction.begin(game, "mira", "What happened last night?"))
    assert out["ok"]
    iid = out["interaction_id"]
    assert run(lambda: interaction.start_npc(game, iid, 1))["ok"]
    return iid


def finalize(game, run, iid):
    return run(lambda: interaction.finalize(game, iid, referee.code_check))


def test_accept_and_idempotent_finalize(lifecycle):
    game, run = lifecycle
    iid = begin(game, run)
    assert run(lambda: interaction.stage(game, iid, 1, "reveal_clue", {"clue_id": "C3"}))["ok"]
    assert "C3" not in state.found_clue_ids(game)
    text = rules_text = game.conn.execute("SELECT text FROM clues WHERE id='C3'").fetchone()[0]
    assert run(lambda: interaction.submit_draft(game, iid, 1, text))["ok"]
    assert run(lambda: interaction.submit_verdict(game, iid, 1, "OK"))["ok"]
    out = finalize(game, run, iid)
    assert out["reply"] == rules_text and out["new_clues"] == ["C3"]
    count = len(events.log(game.conn, game.run_id))
    assert finalize(game, run, iid)["already_finalized"]
    assert len(events.log(game.conn, game.run_id)) == count
    assert state.read(game)["clock"] == 370


def test_reject_discards_staging_and_retries_then_fallback(lifecycle):
    game, run = lifecycle
    iid = begin(game, run)
    for attempt in range(1, 4):
        if attempt > 1:
            assert run(lambda: interaction.start_npc(game, iid, attempt))["ok"]
        assert run(lambda: interaction.stage(game, iid, attempt, "reveal_clue", {"clue_id": "C3"}))["ok"]
        assert run(lambda: interaction.submit_draft(game, iid, attempt, "I saw nothing."))["ok"]
        assert run(lambda: interaction.submit_verdict(game, iid, attempt, "LEAK"))["ok"]
        out = finalize(game, run, iid)
        assert "C3" not in state.found_clue_ids(game)
        assert out["status"] == ("retry" if attempt < 3 else "committed")
    assert out["fallback"] and out["attempts"] == 3
    assert len(state.conversation(game, "mira")) == 2


def test_duplicate_drafts_and_verdicts_and_wrong_state(lifecycle):
    game, run = lifecycle
    iid = begin(game, run)
    assert not run(lambda: interaction.submit_verdict(game, iid, 1, "OK"))["ok"]
    for text, ok in [("hello", True), ("hello", True), ("changed", False)]:
        assert run(lambda: interaction.submit_draft(game, iid, 1, text))["ok"] == ok
    for verdict, ok in [("OK", True), ("OK", True), ("LEAK", False)]:
        assert run(lambda: interaction.submit_verdict(game, iid, 1, verdict))["ok"] == ok
    assert interaction.validate(game, iid, 2, ("evaluating",)) == "wrong attempt"
    assert interaction.validate(game, "missing", 1, ("drafting",)) == "unknown interaction"
    assert not run(lambda: interaction.begin(game, "mira", "again"))["ok"]
    finalize(game, run, iid)
    assert interaction.validate(game, iid, 1, ("drafting",)) == "stale interaction"
    assert not run(lambda: interaction.submit_draft(game, iid, 1, "hello"))["ok"]


@pytest.mark.parametrize("verdict,outcome", [(None, "no_verdict"), ("INVALID", "invalid_verdict"), ("LEAK", "model_leak")])
def test_distinct_fail_closed_outcomes(lifecycle, verdict, outcome):
    game, run = lifecycle
    iid = begin(game, run)
    run(lambda: interaction.submit_draft(game, iid, 1, "Nothing to report."))
    if verdict:
        run(lambda: interaction.submit_verdict(game, iid, 1, verdict))
    assert finalize(game, run, iid)["reason_code"] == outcome


def test_pending_and_empty_draft_retry(lifecycle):
    game, run = lifecycle
    iid = run(lambda: interaction.begin(game, "mira", "hello"))["interaction_id"]
    assert finalize(game, run, iid)["reason_code"] == "no_draft"
    run(lambda: interaction.start_npc(game, iid, 2))
    run(lambda: interaction.submit_draft(game, iid, 2, "  "))
    assert finalize(game, run, iid)["reason_code"] == "no_draft"
    assert interaction.attempt_row(game, iid, 3)["note"] == interaction.CHECK_FAILED_NOTE


def test_overlay_trust_is_local_and_invalid_actions_fail_closed(lifecycle):
    game, run = lifecycle
    run(lambda: interaction.begin(game, "mira", "hello"))
    run(lambda: interaction.recover(game))
    iid = begin(game, run)
    assert run(lambda: interaction.stage(game, iid, 1, "update_trust", {"reason": "polite_repeat"}))["ok"]
    assert state.trust_of(game, "mira") == 0

    def inspect():
        with interaction.overlay(game, interaction.attempt_row(game, iid, 1)):
            assert state.trust_of(game, "mira") == 1
        assert state.trust_of(game, "mira") == 0
        return {"ok": True}

    run(inspect)
    assert not run(lambda: interaction.stage(game, iid, 1, "reveal_clue", {"clue_id": "C5"}))["ok"]
    finalize(game, run, iid)
    assert state.trust_of(game, "mira") == 0
    assert json.loads(interaction.attempt_row(game, iid, 2)["staged"]) == []


def test_replay_and_hash_cover_lifecycle(lifecycle, case):
    game, run = lifecycle
    iid = begin(game, run)
    run(lambda: interaction.stage(game, iid, 1, "reveal_clue", {"clue_id": "C3"}))
    run(lambda: interaction.recover(game))
    conn = schema.connect()
    schema.create_schema(conn)
    insert_case(conn, case)
    events.replay(conn, game.run_id, events.log(game.conn, game.run_id))
    assert events.state_hash(conn, game.run_id) == events.state_hash(game.conn, game.run_id)
    assert interaction.get(game, iid)["outcome"] == "interrupted"
    count = len(events.log(game.conn, game.run_id))
    assert interaction.recover(game) is None
    assert len(events.log(game.conn, game.run_id)) == count


def test_write_guard_and_unknown_finalize(lifecycle):
    game, run = lifecycle
    with pytest.raises(events.WriteOutsideTool):
        interaction.begin(game, "mira", "hello")
    assert not finalize(game, run, "missing")["ok"]
