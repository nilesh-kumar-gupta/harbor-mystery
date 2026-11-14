"""Validation questions the tools ask before anything changes: which place or
character a name means, which exits exist, who is present, which words match."""

from __future__ import annotations

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
    return state.read(game)["clock"] >= state.clock_minutes(state.case_meta(game)["end_clock"])
