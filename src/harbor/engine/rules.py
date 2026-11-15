"""Validation questions the tools ask before anything changes: which place or
character a name means, which exits exist, who is present, which words match."""

from __future__ import annotations

import json
import re
import sqlite3

from harbor.engine import state
from harbor.engine.state import Game


def _norm(text: str) -> str:
    text = re.sub(r"^\s*the\s+", "", str(text).lower())
    return re.sub(r"[^a-z0-9]", "", text)


def _resolve(rows: list[sqlite3.Row], text: str) -> sqlite3.Row | None:
    """Match free text to one row by id or name; a partial match must be unique."""
    q = _norm(text)
    if not q:
        return None
    for r in rows:
        if q in (_norm(r["id"]), _norm(r["name"])):
            return r
    partial = [r for r in rows if q in _norm(r["name"]) or q in _norm(r["id"])]
    return partial[0] if len(partial) == 1 else None


def resolve_place(game: Game, text: str) -> sqlite3.Row | None:
    return _resolve(game.conn.execute("SELECT * FROM places ORDER BY rowid").fetchall(), text)


def resolve_character(game: Game, text: str) -> sqlite3.Row | None:
    return _resolve(game.conn.execute("SELECT * FROM characters ORDER BY rowid").fetchall(), text)


def place(game: Game, place_id: str) -> sqlite3.Row:
    return game.conn.execute("SELECT * FROM places WHERE id = ?", (place_id,)).fetchone()


def exits(game: Game, place_id: str) -> list[sqlite3.Row]:
    """Places one move away, in case-file order."""
    return game.conn.execute(
        "SELECT p.* FROM exits e JOIN places p ON p.id = e.to_place "
        "WHERE e.from_place = ? ORDER BY e.rowid", (place_id,)).fetchall()


def character_place(game: Game, character_id: str) -> str:
    """Where a character is now. Schedules are empty (ADR 001 #7), so: where they start."""
    return game.conn.execute(
        "SELECT start_place FROM characters WHERE id = ?", (character_id,)).fetchone()[0]


def present(game: Game, place_id: str) -> list[sqlite3.Row]:
    rows = game.conn.execute("SELECT * FROM characters ORDER BY rowid").fetchall()
    return [r for r in rows if character_place(game, r["id"]) == place_id]


def visible_clues(game: Game, place_id: str) -> list[sqlite3.Row]:
    return game.conn.execute(
        "SELECT * FROM clues WHERE found_at_place = ? ORDER BY rowid", (place_id,)).fetchall()


def matches(text: str, terms) -> list[str]:
    """The terms that occur in `text` at the start of a word, ignoring case.

    Crude on purpose (ADR 001): "boat" matches "boats", "night" does not match "midnight".
    """
    low = text.lower()
    return [t for t in terms if re.search(r"(?<![a-z0-9])" + re.escape(t.lower()), low)]


def past_end(game: Game) -> bool:
    """True once the clock has reached the end of the day (18:00 in Saltmere)."""
    return state.read(game)["clock"] >= state.clock_minutes(state.case_meta(game)["end_clock"])


# ---- knowledge, topics and trust (M3) ---------------------------------------
#
# Everything below takes the character id from the caller, and the only callers
# are the NPC tools, which pass game_state.active_character. No model-chosen
# value ever selects whose knowledge is read.

MAX_TRUST = 2
TRUST_REASONS = ("polite_repeat", "related_clue_shown", "rude")
COURTESY = ("please", "thank", "kindly", "sorry", "appreciate", "would you", "could you",
            "if you don't mind", "pardon")


def _list(text: str) -> list[str]:
    return json.loads(text)


def clue(game: Game, clue_id: str) -> sqlite3.Row | None:
    return game.conn.execute("SELECT * FROM clues WHERE id = ?", (clue_id,)).fetchone()


def knowledge(game: Game, character_id: str) -> list[sqlite3.Row]:
    return game.conn.execute(
        "SELECT k.*, c.text FROM knowledge k JOIN clues c ON c.id = k.clue_id "
        "WHERE k.character_id = ? ORDER BY k.rowid", (character_id,)).fetchall()


def shown_clues(game: Game, text: str) -> set[str]:
    """Notebook clues the text refers to, by clue keywords (ADR 001 #3)."""
    found = state.found_clue_ids(game)
    rows = game.conn.execute("SELECT id, keywords FROM clues ORDER BY rowid").fetchall()
    return {r["id"] for r in rows if r["id"] in found and matches(text, _list(r["keywords"]))}


def _trust_ok(game: Game, row: sqlite3.Row, character_id: str) -> bool:
    if state.trust_of(game, character_id) >= row["min_trust"]:
        return True
    needed = set(_list(row["unlock_if_shown"]))
    return bool(needed) and needed <= shown_to(game, character_id)


def shown_to(game: Game, character_id: str, since_last: str | None = None) -> set[str]:
    """Clues the player has shown this character today (or in this conversation),
    as recorded when each line was said, not re-read against today's notebook."""
    return {c for e in state.events_of(game, "clues_shown", since_last=since_last)
            if e["character"] == character_id for c in e["clue_ids"]}


def _topic_ok(game: Game, row: sqlite3.Row, message: str) -> bool:
    topics = _list(row["unlock_topics"])
    return (not topics or bool(matches(message, topics))
            or row["clue_id"] == state.read(game)["nudge_clue"])


def _current_message(game: Game, character_id: str) -> str:
    lines = state.player_lines(game, character_id)
    return lines[-1] if lines else ""


def _trust_clues_shown(game: Game, character_id: str) -> list[str]:
    """Related clues in the current message that have not yet earned a trust step here."""
    related = {c for row in knowledge(game, character_id) for c in _list(row["related_clues"])}
    used = {e["clue_id"] for e in state.events_of(game, "trust_changed")
            if e["character"] == character_id and e["reason"] == "related_clue_shown"}
    shown = shown_to(game, character_id, since_last="talk_started")
    return sorted((shown & related) - used)


def recall(game: Game, character_id: str) -> dict:
    """What this character can tell the player right now: unlocked clues and pointers."""
    message = _current_message(game, character_id)
    found = state.found_clue_ids(game)
    nudge = state.read(game)["nudge_clue"]
    clues, guarded = [], False
    for row in knowledge(game, character_id):
        if not _topic_ok(game, row, message):
            continue
        if not _trust_ok(game, row, character_id):
            guarded = guarded or row["clue_id"] not in found
            continue
        item = {"id": row["clue_id"], "text": row["text"]}
        if row["clue_id"] in found:
            item["in_notebook"] = True
        if row["clue_id"] == nudge:
            item["nudge"] = True
        clues.append(item)
    pointers = []
    for p in game.conn.execute(
            "SELECT p.clue_id, p.line, c.name FROM pointers p "
            "JOIN characters c ON c.id = p.points_to_character "
            "WHERE p.character_id = ? ORDER BY p.rowid", (character_id,)):
        if p["clue_id"] in found:
            continue
        item = {"clue": p["clue_id"], "ask": p["name"], "line": p["line"]}
        if p["clue_id"] == nudge:
            item["nudge"] = True
        pointers.append(item)
    from harbor.engine import hints  # hints builds on rules; imported here to avoid a cycle
    extra = hints.nudge_pointer(game, character_id)
    if extra and extra["clue"] not in found:
        pointers.append(extra)
    return {"clues": clues, "pointers": pointers, "guarded": guarded,
            "trust": state.trust_of(game, character_id),
            "shown": _trust_clues_shown(game, character_id)}


def check_reveal(game: Game, character_id: str, clue_id: str) -> str | None:
    """Why this character may not reveal this clue now, or None if they may."""
    if clue(game, clue_id) is None:
        return f"unknown clue {clue_id!r}"
    row = next((r for r in knowledge(game, character_id) if r["clue_id"] == clue_id), None)
    if row is None:
        return f"you do not know clue {clue_id}"
    if not _trust_ok(game, row, character_id):
        return "trust too low"
    return None


def trust_step(game: Game, character_id: str, reason: str) -> tuple[int | None, str | None, str]:
    """Decide a trust change. Returns (new level, clue that earned it, "") or (None, None, why not).

    The model only names a reason; whether trust moves is decided here, one step
    at most and once per conversation.
    """
    level = state.trust_of(game, character_id)
    if any(e["character"] == character_id
           for e in state.events_of(game, "trust_changed", since_last="talk_started")):
        return None, None, "trust already changed in this conversation"
    earlier = [e for e in state.events_of(game, "trust_changed") if e["character"] == character_id]
    if reason == "rude":
        if level == 0:
            return None, None, "trust is already at its lowest"
        return level - 1, None, ""
    if level >= MAX_TRUST:
        return None, None, "trust is already at its highest"
    if reason == "polite_repeat":
        if len(state.player_lines(game, character_id)) < 2:
            return None, None, "the player has not asked before"
        if not matches(_current_message(game, character_id), COURTESY):
            return None, None, "the player did not ask politely"
        if any(e["reason"] == "polite_repeat" for e in earlier):
            return None, None, "a polite repeat has already been counted"
        return level + 1, None, ""
    if reason == "related_clue_shown":
        shown = _trust_clues_shown(game, character_id)
        if not shown:
            return None, None, "no related clue from the notebook was shown"
        return level + 1, shown[0], ""
    return None, None, f"unknown reason {reason!r}"
