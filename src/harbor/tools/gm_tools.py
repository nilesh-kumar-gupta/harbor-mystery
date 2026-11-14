"""The game master's tools. Each validates through the engine, then changes
state through it, and returns plain JSON."""

from __future__ import annotations

from harbor.engine import rules, state
from harbor.tools.registry import REGISTRY, Context, error

LOOK_MINUTES = 5
GO_MINUTES = 10


def _names(rows) -> list[str]:
    return [r["name"] for r in rows]


@REGISTRY.tool("look", "gm",
               "Look around the current place: description, visible clues, who is here, "
               "exits and the clock. Visible clues go into the notebook. Costs 5 minutes.")
def look(ctx: Context) -> dict:
    game = ctx.game
    st = state.read(game)
    here = rules.place(game, st["place"])
    clues = rules.visible_clues(game, here["id"])
    clock = state.advance_clock(game, LOOK_MINUTES)  # first, so finds are stamped with the
    for clue in clues:                               # time the action ends (ADR 002 #8)
        state.add_clue(game, clue["id"], None)
    return {
        "ok": True, "place": here["name"], "description": here["description"],
        "present": _names(rules.present(game, here["id"])),
        "clues": [{"id": c["id"], "text": c["text"]} for c in clues],
        "exits": _names(rules.exits(game, here["id"])),
        "clock": state.clock_text(clock),
    }


@REGISTRY.tool("go", "gm",
               "Move the player to a neighbouring place. Costs 10 minutes.",
               {"place": {"type": "string", "description": "Name or id of the place"}},
               required=("place",))
def go(ctx: Context, place: str) -> dict:
    game = ctx.game
    st = state.read(game)
    here = rules.place(game, st["place"])
    exits = rules.exits(game, here["id"])
    target = rules.resolve_place(game, place)
    if target is None:
        return error(f"unknown place {place!r}", exits=_names(exits))
    if target["id"] == here["id"]:
        return error(f"already at {here['name']}", exits=_names(exits))
    if target["id"] not in [e["id"] for e in exits]:
        return error(f"no exit from {here['name']} to {target['name']}", exits=_names(exits))
    state.move(game, target["id"])
    clock = state.advance_clock(game, GO_MINUTES)
    return {"ok": True, "place": target["name"],
            "present": _names(rules.present(game, target["id"])),
            "clock": state.clock_text(clock)}


@REGISTRY.tool("notebook", "gm", "List the clues found so far, with source and place. Free.")
def notebook(ctx: Context) -> dict:
    return {"ok": True, "clues": [
        {"id": r["clue_id"], "text": r["text"], "source": r["source_name"] or "seen",
         "place": r["place_name"], "at": state.clock_text(r["minute"])}
        for r in state.notebook(ctx.game)]}
