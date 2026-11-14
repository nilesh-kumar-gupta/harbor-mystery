"""The append-only event log, the reducer that turns events into state, replay
and the state hash.

Every change to run data goes through `record`: it appends one row to `events`
and applies it with the reducer `_apply`. Replaying the same rows through it on a
fresh case load rebuilds the same state, which is the determinism test.
"""

from __future__ import annotations

import hashlib
import json

HASHED_TABLES = {
    "game_state": "clock, place, active_character, turn, turns_since_new_clue, "
                  "nudge_clue, status, result",
    "trust": "character_id, level",
    "notebook": "clue_id, source_character, place, minute, turn",
    "conversations": "character_id, turn, speaker, text",
}


class WriteOutsideTool(RuntimeError):
    """Something tried to change run state while no tool call was in progress."""


def record(game, type: str, payload: dict) -> None:
    """Append one event and apply it. Allowed only while a tool call is running."""
    if game.tool_depth < 1:
        raise WriteOutsideTool(f"event {type!r} recorded outside a tool call")
    row = game.conn.execute(
        "SELECT turn FROM game_state WHERE run_id = ?", (game.run_id,)).fetchone()
    turn = row["turn"] if row else 0
    game.conn.execute(
        "INSERT INTO events (run_id, turn, type, payload, span_id) VALUES (?,?,?,?,?)",
        (game.run_id, turn, type, json.dumps(payload, sort_keys=True), game.span_id),
    )
    _apply(game.conn, game.run_id, turn, type, payload)


def _apply(conn, run_id: str, turn: int, type: str, p: dict) -> None:
    """The reducer: the only code that writes game_state, trust, notebook or conversations."""
    if type == "run_started":
        conn.execute(
            "INSERT INTO game_state (run_id, clock, place, active_character, turn, "
            "turns_since_new_clue, nudge_clue, status, result) "
            "VALUES (?,?,?,NULL,1,0,NULL,'active',NULL)",
            (run_id, p["clock"], p["place"]),
        )
        conn.executemany("INSERT INTO trust VALUES (?,?,0)",
                         [(run_id, cid) for cid in p["characters"]])
    elif type == "clock_advanced":
        _set(conn, run_id, clock=p["clock"])
    elif type == "player_moved":
        _set(conn, run_id, place=p["to"], active_character=None)
    elif type == "clue_found":
        conn.execute(
            "INSERT INTO notebook VALUES (?,?,?,?,?,?)",
            (run_id, p["clue_id"], p["source_character"], p["place"], p["minute"], turn),
        )
    elif type == "talk_started":
        _set(conn, run_id, active_character=p["character"])
    elif type == "line_said":
        conn.execute(
            "INSERT INTO conversations (run_id, character_id, turn, speaker, text) "
            "VALUES (?,?,?,?,?)",
            (run_id, p["character"], turn, p["speaker"], p["text"]),
        )
    elif type == "trust_changed":
        conn.execute("UPDATE trust SET level = ? WHERE run_id = ? AND character_id = ?",
                     (p["to"], run_id, p["character"]))
    elif type == "nudge_armed":
        _set(conn, run_id, nudge_clue=p["clue_id"])
    elif type == "turn_ended":
        _set(conn, run_id, turn=turn + 1, turns_since_new_clue=p["turns_since_new_clue"])
        if p.get("nudge_cleared"):
            _set(conn, run_id, nudge_clue=None)
    elif type == "game_ended":
        _set(conn, run_id, status="ended", result=p["result"])
    elif type in ("clue_confirmed", "hint_used"):
        pass  # logged for the referee and for analysis; no state of their own
    else:
        raise ValueError(f"unknown event type {type!r}")


def _set(conn, run_id: str, **cols) -> None:
    names = ", ".join(f"{name} = ?" for name in cols)
    conn.execute(f"UPDATE game_state SET {names} WHERE run_id = ?", (*cols.values(), run_id))


def log(conn, run_id: str) -> list[dict]:
    """The run's events in order, payloads decoded."""
    rows = conn.execute(
        "SELECT seq, turn, type, payload, span_id FROM events WHERE run_id = ? ORDER BY seq",
        (run_id,),
    ).fetchall()
    return [{"seq": r["seq"], "turn": r["turn"], "type": r["type"],
             "payload": json.loads(r["payload"]), "span_id": r["span_id"]} for r in rows]


def replay(conn, run_id: str, events: list[dict]) -> None:
    """Rebuild run state in `conn` (case tables loaded, run tables empty) from a log."""
    for e in events:
        _apply(conn, run_id, e["turn"], e["type"], e["payload"])


def state_hash(conn, run_id: str) -> str:
    """A digest of all run state. The run id itself is left out so runs can be compared."""
    digest = hashlib.sha256()
    for table, cols in HASHED_TABLES.items():
        order = "id" if table == "conversations" else cols
        rows = conn.execute(
            f"SELECT {cols} FROM {table} WHERE run_id = ? ORDER BY {order}", (run_id,)
        ).fetchall()
        digest.update(json.dumps([table, [list(r) for r in rows]]).encode("utf-8"))
    return digest.hexdigest()
