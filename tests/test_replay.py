"""Determinism and replay: two runs with one seed give the same final state hash,
and so does a replay of the saved event log on a fresh case load."""

from harbor.bot import Bot
from harbor.engine import events, state
from harbor.offline import offline_llms
from harbor.session import Session


def bot_run(case, run_id, seed=7):
    session = Session.create(case, run_id, offline_llms(), seed=seed)
    Bot(session).play()
    return session.ctx.game


def test_two_runs_with_one_seed_end_in_the_same_state(case):
    a, b = bot_run(case, "run-a"), bot_run(case, "run-b")
    assert state.read(a)["status"] == "ended"
    assert events.state_hash(a.conn, a.run_id) == events.state_hash(b.conn, b.run_id)
    strip = lambda log: [(e["turn"], e["type"], e["payload"]) for e in log]
    assert strip(events.log(a.conn, a.run_id)) == strip(events.log(b.conn, b.run_id))


def test_a_replay_of_the_event_log_gives_the_same_state(case):
    game = bot_run(case, "run-c")
    fresh = state.new_game(case, "replayed")
    events.replay(fresh.conn, fresh.run_id, events.log(game.conn, game.run_id))
    assert events.state_hash(fresh.conn, fresh.run_id) == events.state_hash(game.conn, game.run_id)


def test_every_model_reply_is_saved_for_replay(case):
    """Each llm_call span holds the raw reply the model gave, before validation."""
    game = bot_run(case, "run-d")
    rows = game.conn.execute("SELECT output FROM spans WHERE kind = 'llm_call'").fetchall()
    assert rows and all(r["output"] for r in rows)


def test_the_state_hash_notices_a_difference(case):
    a = bot_run(case, "run-e")
    session = Session.create(case, "run-f", offline_llms(), seed=7)
    session.turn("look")
    assert events.state_hash(a.conn, a.run_id) != \
        events.state_hash(session.ctx.game.conn, session.ctx.game.run_id)
