"""Read and change the run's state: game_state, the clock, the notebook.

Readers query the database. Every change is one `events.record` call, so the
event log stays complete; nothing here writes a run table directly.
"""

from __future__ import annotations

import json
import random
import sqlite3
from dataclasses import dataclass, field

from harbor.engine import events, schema
from harbor.engine.case_loader import Case, insert_case


@dataclass
class Game:
    """One run: its database plus the bookkeeping the write guard needs."""

    conn: sqlite3.Connection
    run_id: str
    rng: random.Random = field(default_factory=random.Random)
    tool_depth: int = 0          # > 0 while a tool call runs; events.record checks it
    span_id: str | None = None   # the enclosing tool_call span, stamped on each event
    tracer: object = None        # the run's one tracer, kept here so every Context shares it


def new_game(case: Case, run_id: str, seed: int = 0, db_path: str = ":memory:",
             git_commit: str | None = None, prompt_hash: str | None = None,
             model: str | None = None) -> Game:
    """Create a run database: schema, the case tables and the runs row.

    Run state itself is created by the `start_run` engine step, as an event.
    """
    conn = schema.connect(db_path)
    schema.create_schema(conn)
    insert_case(conn, case)
    conn.execute("INSERT INTO runs VALUES (?,?,?,?,?,?)",
                 (run_id, seed, case.case_id, git_commit, prompt_hash, model))
    return Game(conn=conn, run_id=run_id, rng=random.Random(seed))


def clock_text(minutes: int) -> str:
    return f"{minutes // 60:02d}:{minutes % 60:02d}"


def clock_minutes(text: str) -> int:
    hours, minutes = text.split(":")
    return int(hours) * 60 + int(minutes)


# ---- readers -------------------------------------------------------------

def read(game: Game) -> sqlite3.Row | None:
    """The single game_state row, or None before start_run."""
    return game.conn.execute(
        "SELECT * FROM game_state WHERE run_id = ?", (game.run_id,)).fetchone()


def case_meta(game: Game) -> sqlite3.Row:
    return game.conn.execute("SELECT * FROM case_meta").fetchone()


def is_over(game: Game) -> bool:
    st = read(game)
    return st is not None and st["status"] == "ended"


def notebook(game: Game) -> list[sqlite3.Row]:
    """Clues found so far with their text, source name and place name, oldest first."""
    return game.conn.execute(
        "SELECT n.clue_id, c.text, n.source_character, ch.name AS source_name, "
        "p.name AS place_name, n.minute, n.turn "
        "FROM notebook n JOIN clues c ON c.id = n.clue_id "
        "JOIN places p ON p.id = n.place "
        "LEFT JOIN characters ch ON ch.id = n.source_character "
        "WHERE n.run_id = ? ORDER BY n.minute, n.rowid", (game.run_id,),
    ).fetchall()


def found_clue_ids(game: Game) -> set[str]:
    rows = game.conn.execute(
        "SELECT clue_id FROM notebook WHERE run_id = ?", (game.run_id,)).fetchall()
    return {r["clue_id"] for r in rows}


def clues_found_in_turn(game: Game, turn: int) -> int:
    return game.conn.execute(
        "SELECT COUNT(*) FROM notebook WHERE run_id = ? AND turn = ?",
        (game.run_id, turn)).fetchone()[0]


# ---- changes (each is one event) -------------------------------------------

def start(game: Game) -> None:
    meta = case_meta(game)
    chars = [r["id"] for r in game.conn.execute("SELECT id FROM characters ORDER BY rowid")]
    events.record(game, "run_started", {
        "place": meta["start_place"], "clock": clock_minutes(meta["start_clock"]),
        "characters": chars})


def advance_clock(game: Game, minutes: int) -> int:
    clock = read(game)["clock"] + minutes
    events.record(game, "clock_advanced", {"minutes": minutes, "clock": clock})
    return clock


def move(game: Game, to_place: str) -> None:
    events.record(game, "player_moved", {"from": read(game)["place"], "to": to_place})


def add_clue(game: Game, clue_id: str, source_character: str | None) -> bool:
    """Put a clue in the notebook. Returns False (and logs a confirmation) if already there."""
    st = read(game)
    if clue_id in found_clue_ids(game):
        if source_character is not None:
            events.record(game, "clue_confirmed",
                          {"clue_id": clue_id, "source_character": source_character})
        return False
    events.record(game, "clue_found", {
        "clue_id": clue_id, "source_character": source_character,
        "place": st["place"], "minute": st["clock"]})
    return True


def finish_turn(game: Game, turns_since_new_clue: int, nudge_cleared: bool) -> None:
    events.record(game, "turn_ended", {
        "turns_since_new_clue": turns_since_new_clue, "nudge_cleared": nudge_cleared})


def end_game(game: Game, result: str, details: dict | None = None) -> None:
    events.record(game, "game_ended", {"result": result, **(details or {})})


# ---- conversations and trust (M3) -------------------------------------------

def trust_of(game: Game, character_id: str) -> int:
    return game.conn.execute(
        "SELECT level FROM trust WHERE run_id = ? AND character_id = ?",
        (game.run_id, character_id)).fetchone()[0]


def conversation(game: Game, character_id: str) -> list[sqlite3.Row]:
    """Today's lines between the player and one character, oldest first."""
    return game.conn.execute(
        "SELECT turn, speaker, text FROM conversations "
        "WHERE run_id = ? AND character_id = ? ORDER BY id", (game.run_id, character_id),
    ).fetchall()


def player_lines(game: Game, character_id: str) -> list[str]:
    return [r["text"] for r in conversation(game, character_id) if r["speaker"] == "player"]


def spoken_to(game: Game) -> set[str]:
    rows = game.conn.execute(
        "SELECT DISTINCT character_id FROM conversations WHERE run_id = ?", (game.run_id,))
    return {r["character_id"] for r in rows}


def events_of(game: Game, type: str, since_last: str | None = None) -> list[dict]:
    """Payloads of this run's events of one type, optionally only those after the
    latest event of type `since_last`."""
    floor = 0
    if since_last:
        floor = game.conn.execute(
            "SELECT COALESCE(MAX(seq), 0) FROM events WHERE run_id = ? AND type = ?",
            (game.run_id, since_last)).fetchone()[0]
    rows = game.conn.execute(
        "SELECT payload FROM events WHERE run_id = ? AND type = ? AND seq > ? ORDER BY seq",
        (game.run_id, type, floor)).fetchall()
    return [json.loads(r["payload"]) for r in rows]


def start_talk(game: Game, character_id: str) -> None:
    events.record(game, "talk_started", {"character": character_id})


def say(game: Game, character_id: str, speaker: str, text: str) -> None:
    events.record(game, "line_said",
                  {"character": character_id, "speaker": speaker, "text": text})


def change_trust(game: Game, character_id: str, to: int, reason: str,
                 clue_id: str | None = None) -> None:
    events.record(game, "trust_changed", {
        "character": character_id, "from": trust_of(game, character_id), "to": to,
        "reason": reason, "clue_id": clue_id})
