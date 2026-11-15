"""Scoring an accusation against case_solution. The only reader of that table:
no agent ever sees it, and the true timeline leaves the engine only inside the
ending, after the game is over."""

from __future__ import annotations

import json

from harbor.engine import state
from harbor.engine.state import Game


def _solution(game: Game) -> dict:
    row = game.conn.execute("SELECT * FROM case_solution").fetchone()
    return {"culprit_id": row["culprit_id"], "motive": row["motive"], "method": row["method"],
            "true_timeline": json.loads(row["true_timeline"]),
            "required": json.loads(row["required_clue_ids"]),
            "min_supporting": row["min_supporting"]}


def score(game: Game, character_id: str, clue_ids: list[str]) -> dict:
    """win: right culprit and at least min_supporting required clues; partial: right
    culprit with fewer; lose: anyone else. The motive does not count (ADR 001 #9)."""
    sol = _solution(game)
    correct = character_id == sol["culprit_id"]
    supporting = [c for c in clue_ids if c in sol["required"]]
    if not correct:
        result = "lose"
    elif len(supporting) >= sol["min_supporting"]:
        result = "win"
    else:
        result = "partial"
    return {"result": result, "culprit_correct": correct, "supporting": supporting,
            **ending(game)}


def ending(game: Game) -> dict:
    """What the ending reveals: the culprit, the true motive and timeline, and which
    of the required clues were found or missed."""
    sol = _solution(game)
    found = state.found_clue_ids(game)
    name = game.conn.execute("SELECT name FROM characters WHERE id = ?",
                             (sol["culprit_id"],)).fetchone()["name"]
    return {"culprit": name, "true_motive": sol["motive"], "true_method": sol["method"],
            "true_timeline": sol["true_timeline"],
            "found": [c for c in sol["required"] if c in found],
            "missed": [c for c in sol["required"] if c not in found]}
