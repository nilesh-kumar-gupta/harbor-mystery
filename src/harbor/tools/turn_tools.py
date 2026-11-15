"""Explicit turn boundaries shared by offline and plugin orchestrators."""
from harbor.engine import state
from harbor.tools.registry import REGISTRY, error
from harbor.tools.gm_tools import state_summary
from harbor.tools.steps import end_turn


@REGISTRY.tool("player_turn", "gm", "Start a player turn.", {"line": {"type": "string"}}, ("line",))
def player_turn(ctx, line):
    ctx.turn_open = True
    turn = state.read(ctx.game)["turn"]
    ctx.tracer.open_virtual("agent", "game_master", f"{ctx.tracer.trace_id}-gm", input=line,
                            attrs={"game.turn": turn, "gen_ai.agent.name": "game_master"})
    return {"ok": True, "turn": turn, "trace_id": ctx.tracer.trace_id,
            "state": state_summary(ctx)}


@REGISTRY.tool("close_turn", "gm", "Close the current player turn.", when_ended=True)
def close_turn(ctx):
    if not ctx.turn_open:
        return error("no turn is open")
    out = end_turn(ctx)
    ctx.turn_open = False
    return out
