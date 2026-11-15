"""Hints: the stuck nudge and the `hint` target. The engine picks the clue and
the person; a model only ever phrases what this module chose."""

from __future__ import annotations

import json
from collections import deque

from harbor.engine import events, rules, state
from harbor.engine.state import Game

STUCK_TURNS = 8


def next_unfound(game: Game) -> str | None:
    """The first clue in the case's nudge order that is not in the notebook."""
    order = json.loads(state.case_meta(game)["nudge_order"])
    found = state.found_clue_ids(game)
    return next((c for c in order if c not in found), None)


def maybe_arm_nudge(game: Game, turns_since_new_clue: int) -> str | None:
    """Called by end_turn: after STUCK_TURNS turns with no new clue, arm the next one."""
    st = state.read(game)
    if turns_since_new_clue < STUCK_TURNS or st["nudge_clue"] is not None:
        return None
    clue_id = next_unfound(game)
    if clue_id is not None:
        events.record(game, "nudge_armed", {"clue_id": clue_id,
                                            "turns_since_new_clue": turns_since_new_clue})
    return clue_id


def distances(game: Game, start: str) -> dict[str, int]:
    """Moves from `start` to every place (breadth-first over exits)."""
    seen, queue = {start: 0}, deque([start])
    while queue:
        here = queue.popleft()
        for nxt in rules.exits(game, here):
            if nxt["id"] not in seen:
                seen[nxt["id"]] = seen[here] + 1
                queue.append(nxt["id"])
    return seen


def knowers(game: Game, clue_id: str) -> list[str]:
    """Characters who know a clue, nearest first; people not yet spoken to first."""
    rows = game.conn.execute(
        "SELECT character_id FROM knowledge WHERE clue_id = ? ORDER BY rowid", (clue_id,))
    ids = [r["character_id"] for r in rows]
    dist = distances(game, state.read(game)["place"])
    spoken = state.spoken_to(game)
    return sorted(ids, key=lambda c: (c in spoken, dist.get(rules.character_place(game, c), 99)))


def nudge_pointer(game: Game, character_id: str) -> dict | None:
    """For the armed nudge, a pointer the active character can give when they do not
    know the clue and have no pointer row for it: the nearest person who does."""
    clue_id = state.read(game)["nudge_clue"]
    if clue_id is None:
        return None
    known = game.conn.execute("SELECT 1 FROM knowledge WHERE character_id = ? AND clue_id = ?",
                              (character_id, clue_id)).fetchone()
    has_row = game.conn.execute("SELECT 1 FROM pointers WHERE character_id = ? AND clue_id = ?",
                                (character_id, clue_id)).fetchone()
    if known or has_row:
        return None
    target = next((c for c in knowers(game, clue_id) if c != character_id), None)
    if target is None:
        return None
    who = game.conn.execute("SELECT name FROM characters WHERE id = ?", (target,)).fetchone()
    return {"clue": clue_id, "ask": who["name"], "line": None, "nudge": True}


def hint_target(game: Game) -> dict | None:
    """The person (and their place) the `hint` tool points to, or None if every
    nudge clue is found. Never the clue's text."""
    order = json.loads(state.case_meta(game)["nudge_order"])
    found = state.found_clue_ids(game)
    candidates = [(c, knowers(game, c)) for c in order if c not in found]
    clue_id, people = next(((c, k) for c, k in candidates if k), (None, []))
    if clue_id is None:
        return None
    character_id = people[0]
    row = game.conn.execute("SELECT name FROM characters WHERE id = ?", (character_id,)).fetchone()
    place = rules.place(game, rules.character_place(game, character_id))
    return {"clue_id": clue_id, "character": row["name"], "place": place["name"]}
