"""The game master's tools. Each validates through the engine, then changes
state through it, and returns plain JSON."""

from __future__ import annotations

from harbor.engine import hints, rules, scoring, state
from harbor.tools.registry import REGISTRY, Context, error

LOOK_MINUTES = 5
GO_MINUTES = 10
TALK_MINUTES = 10
HINT_MINUTES = 20
REFEREE_ATTEMPTS = 3  # the first draft and up to 2 regenerations


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


def state_summary(ctx: Context) -> dict:
    """The compact state the game master sees each turn. Read-only; not a tool."""
    game = ctx.game
    st = state.read(game)
    return {"place": rules.place(game, st["place"])["name"],
            "clock": state.clock_text(st["clock"]),
            "notebook": [f"{r['clue_id']}: {r['text']}" for r in state.notebook(game)],
            "status": st["status"]}


@REGISTRY.tool("hint", "gm",
               "Ask for a hint: the engine names a person and place worth a visit. "
               "Costs 20 minutes.")
def hint(ctx: Context) -> dict:
    game = ctx.game
    target = hints.hint_target(game)
    clock = state.advance_clock(game, HINT_MINUTES)
    state.note_hint(game, target["clue_id"] if target else None)
    shown = {k: target[k] for k in ("character", "place")} if target else None
    return {"ok": True, "target": shown, "clock": state.clock_text(clock)}


@REGISTRY.tool("accuse", "gm",
               "Accuse someone, with the motive in the player's words and the ids of "
               "notebook clues that support it. Ends the game and reveals what happened. Free.",
               {"character": {"type": "string", "description": "Name or id of the accused"},
                "motive": {"type": "string"},
                "clue_ids": {"type": "array", "items": {"type": "string"},
                             "description": "Ids of clues from the notebook"}},
               required=("character", "motive", "clue_ids"))
def accuse(ctx: Context, character: str, motive: str, clue_ids: list[str]) -> dict:
    game = ctx.game
    who = rules.resolve_character(game, character)
    if who is None:
        return error(f"unknown character {character!r}")
    found = state.found_clue_ids(game)
    ids = list(dict.fromkeys(c.strip().upper() for c in clue_ids))  # "c3 " is C3; no repeats
    for clue_id in ids:
        if rules.clue(game, clue_id) is None:
            return error(f"unknown clue {clue_id!r}", notebook=sorted(found))
        if clue_id not in found:
            return error(f"clue {clue_id} is not in the notebook", notebook=sorted(found))
    out = scoring.score(game, who["id"], ids)
    state.end_game(game, out["result"], {"accused": who["id"], "motive": motive,
                                         "clue_ids": ids})
    return {"ok": True, "accused": who["name"], "motive_given": motive, **out}
