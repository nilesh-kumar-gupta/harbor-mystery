"""Durable conversations. Only the event reducer writes lifecycle state.

Callers supply the deterministic referee check to keep engine imports one way.
Overlays exist for one tool call and are always discarded before a handoff.
"""
from __future__ import annotations

import itertools
import json
from contextlib import contextmanager

from harbor.engine import events, rules, state

LEAK_NOTE = "your last reply used facts you do not know. Use only what your tools returned."
CHECK_FAILED_NOTE = "your last reply could not be checked. Reply again, using only what your tools returned."
_serial = itertools.count()


def get(game, interaction_id):
    return game.conn.execute("SELECT * FROM interactions WHERE run_id=? AND interaction_id=?",
                             (game.run_id, interaction_id)).fetchone()


def opened(game):
    return game.conn.execute("SELECT * FROM interactions WHERE run_id=? AND status NOT IN ('committed','abandoned')",
                             (game.run_id,)).fetchone()


def attempt_row(game, interaction_id, attempt):
    return game.conn.execute("SELECT * FROM attempts WHERE interaction_id=? AND attempt=?",
                             (interaction_id, attempt)).fetchone()


def validate(game, interaction_id, attempt, phases, *, active=True):
    row = get(game, interaction_id)
    if row is None:
        return "unknown interaction"
    current = opened(game)
    if current is None or current["interaction_id"] != interaction_id:
        return "stale interaction"
    if row["attempt"] != attempt:
        return "wrong attempt"
    if active and row["character_id"] != state.read(game)["active_character"]:
        return "inactive character"
    if row["status"] not in phases:
        return "wrong interaction state"
    return None


def _record(game, kind, row, **payload):
    events.record(game, kind, {"interaction_id": row["interaction_id"],
                              "attempt": row["attempt"], **payload})


def begin(game, character, message):
    if opened(game):
        return {"ok": False, "error": "finish the conversation first"}
    card = rules.resolve_character(game, character)
    if card is None or rules.character_place(game, card["id"]) != state.read(game)["place"]:
        return {"ok": False, "error": "character not present"}
    count = game.conn.execute("SELECT COUNT(*) FROM interactions WHERE run_id=?", (game.run_id,)).fetchone()[0]
    iid = f"i{count + 1:03d}-{game.rng.getrandbits(16):04x}"
    state.advance_clock(game, 10)
    state.start_talk(game, card["id"])
    state.say(game, card["id"], "player", message)
    state.record_shown(game, card["id"], sorted(rules.shown_clues(game, message)))
    events.record(game, "interaction_begun", {"interaction_id": iid, "character": card["id"], "message": message})
    _record(game, "attempt_started", {"interaction_id": iid, "attempt": 1})
    return {"ok": True, "interaction_id": iid, "attempt": 1, "character": card["name"],
            "clock": state.clock_text(state.read(game)["clock"])}


def start_npc(game, iid, attempt):
    problem = validate(game, iid, attempt, ("pending", "drafting"))
    if problem:
        return {"ok": False, "error": problem}
    row = get(game, iid)
    if row["status"] == "pending":
        _record(game, "npc_started", row)
    return {"ok": True}


def apply_action(game, row, action, args):
    who = row["character_id"]
    if action == "reveal_clue":
        problem = rules.check_reveal(game, who, args["clue_id"])
        if problem:
            raise ValueError(problem)
        state.add_clue(game, args["clue_id"], who)
    elif action == "update_trust":
        level, clue, problem = rules.trust_step(game, who, args["reason"])
        if level is None:
            raise ValueError(problem)
        state.change_trust(game, who, level, args["reason"], clue)
    else:
        raise ValueError("unknown staged action")


@contextmanager
def overlay(game, row):
    name = f"overlay_{next(_serial)}"
    game.conn.execute(f"SAVEPOINT {name}")
    try:
        current = get(game, row["interaction_id"])
        for action in json.loads(row["staged"]):
            apply_action(game, current, action["action"], action["args"])
        yield
    finally:
        game.conn.execute(f"ROLLBACK TO {name}")
        game.conn.execute(f"RELEASE {name}")


def stage(game, iid, attempt, action, args):
    problem = validate(game, iid, attempt, ("drafting",))
    if problem:
        return {"ok": False, "error": problem}
    row = get(game, iid)
    try:
        with overlay(game, attempt_row(game, iid, attempt)):
            apply_action(game, row, action, args)
    except ValueError as exc:
        return {"ok": False, "error": str(exc)}
    _record(game, "action_staged", row, action=action, args=args)
    return {"ok": True, "staged": True}


def submit_draft(game, iid, attempt, text):
    problem = validate(game, iid, attempt, ("drafting", "evaluating"))
    if problem:
        return {"ok": False, "error": problem}
    if not isinstance(text, str):
        return {"ok": False, "error": "draft must be a string"}
    old = attempt_row(game, iid, attempt)["draft"]
    if old is not None:
        if old != text:
            return {"ok": False, "error": "draft already submitted"}
    else:
        _record(game, "draft_submitted", get(game, iid), text=text)
    return {"ok": True, "status": "evaluating"}


def submit_verdict(game, iid, attempt, verdict, reason=""):
    problem = validate(game, iid, attempt, ("evaluating",))
    if problem:
        return {"ok": False, "error": problem}
    if not isinstance(verdict, str) or verdict not in ("OK", "LEAK", "INVALID"):
        return {"ok": False, "error": "verdict must be OK, LEAK or INVALID"}
    row = attempt_row(game, iid, attempt)
    if row["verdict"] is not None:
        if (row["verdict"], row["verdict_reason"]) != (verdict, reason):
            return {"ok": False, "error": "verdict already submitted"}
    else:
        _record(game, "verdict_recorded", get(game, iid), verdict=verdict, reason=reason)
    return {"ok": True, "recorded": verdict}


def stored_result(game, row):
    return {"ok": True, "status": "committed", "reply": row["reply"],
            "fallback": bool(row["fallback"]), "outcome": row["outcome"],
            "new_clues": json.loads(row["new_clues"]), "attempts": row["attempt"],
            "clock": next(e["payload"]["clock"] for e in reversed(events.log(game.conn, game.run_id))
                          if e["type"] in ("interaction_committed", "interaction_recovered")
                          and e["payload"]["interaction_id"] == row["interaction_id"])}


def _commit(game, row, reply, fallback, outcome, new_clues, recovered=False, said=False):
    if not said:
        state.say(game, row["character_id"], "npc", reply)
    _record(game, "interaction_recovered" if recovered else "interaction_committed", row,
            reply=reply, fallback=fallback, outcome=outcome, new_clues=new_clues,
            clock=state.clock_text(state.read(game)["clock"]))
    return stored_result(game, get(game, row["interaction_id"]))


def finalize(game, iid, check, tool_limit=False):
    row = get(game, iid)
    if row is None:
        return {"ok": False, "error": "unknown interaction"}
    if row["status"] in ("committed", "abandoned"):
        return {**stored_result(game, row), "already_finalized": True}
    problem = validate(game, iid, row["attempt"], ("pending", "drafting", "evaluating"), active=False)
    if problem:
        return {"ok": False, "error": problem}
    draft = attempt_row(game, iid, row["attempt"])
    leaked = []
    if row["character_id"] != state.read(game)["active_character"]:
        _record(game, "attempt_closed", row, outcome="interrupted")
        card = rules.resolve_character(game, row["character_id"])
        return _commit(game, row, card["fallback_line"], True, "interrupted", [])
    if tool_limit:
        outcome = "tool_limit"
    elif not draft["draft"] or not draft["draft"].strip():
        outcome = "no_draft"
    else:
        try:
            with overlay(game, draft):
                pass
        except ValueError:
            outcome = "invalid_action"
        else:
            with overlay(game, draft):
                verdict = check(game, row["character_id"], row["player_message"], draft["draft"])
            leaked = verdict.leaked
            outcome = ("code_leak" if not verdict.ok else
                       {"OK": "accepted", "LEAK": "model_leak", "INVALID": "invalid_verdict", None: "no_verdict"}.get(draft["verdict"], "invalid_verdict"))
    before = state.found_clue_ids(game)
    if outcome == "accepted":
        for action in json.loads(draft["staged"]):
            apply_action(game, row, action["action"], action["args"])
    if outcome == "accepted":
        state.say(game, row["character_id"], "npc", draft["draft"])
    _record(game, "attempt_closed", row, outcome=outcome, leaked=leaked)
    if outcome == "accepted":
        return _commit(game, row, draft["draft"], False, outcome,
                       sorted(state.found_clue_ids(game) - before), said=True)
    if row["attempt"] < 3:
        note = LEAK_NOTE if outcome in ("code_leak", "model_leak") else CHECK_FAILED_NOTE
        _record(game, "attempt_started", {"interaction_id": iid, "attempt": row["attempt"] + 1}, note=note)
        return {"ok": True, "status": "retry", "attempt": row["attempt"] + 1, "reason_code": outcome}
    card = rules.resolve_character(game, row["character_id"])
    return _commit(game, row, card["fallback_line"], True, outcome, [])


def recover(game):
    row = opened(game)
    if row is None:
        return None
    _record(game, "attempt_closed", row, outcome="interrupted")
    card = rules.resolve_character(game, row["character_id"])
    return _commit(game, row, card["fallback_line"], True, "interrupted", [], recovered=True)
