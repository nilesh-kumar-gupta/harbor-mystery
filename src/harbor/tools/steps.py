"""Engine steps: state changes that no model asks for (ADR 001 #8).

They are registered like tools, with audience "engine", so they pass through
the same guard and are traced as tool calls, but they are never offered to a model.
"""

from __future__ import annotations

from harbor.engine import rules, state
from harbor.tools.registry import REGISTRY, Context, error


@REGISTRY.tool("start_run", "engine", "Create the run's initial state.")
def start_run(ctx: Context) -> dict:
    game = ctx.game
    if state.read(game) is not None:
        return error("run already started")
    state.start(game)
    st = state.read(game)
    return {"ok": True, "place": rules.place(game, st["place"])["name"],
            "clock": state.clock_text(st["clock"])}


@REGISTRY.tool("end_turn", "engine",
               "Close the turn: stuck counter, turn number, and the end of the day.",
               when_ended=True)
def end_turn(ctx: Context) -> dict:
    game = ctx.game
    st = state.read(game)
    if st["status"] == "ended":
        return {"ok": True, "status": "ended", "result": st["result"]}
    new_clue = state.clues_found_in_turn(game, st["turn"]) > 0
    stuck = 0 if new_clue else st["turns_since_new_clue"] + 1
    state.finish_turn(game, stuck, nudge_cleared=new_clue)
    out = {"ok": True, "status": "active", "turn": st["turn"] + 1,
           "clock": state.clock_text(st["clock"]), "turns_since_new_clue": stuck}
    if rules.past_end(game):
        state.end_game(game, "timeout")
        out.update(status="ended", result="timeout")
    return out
