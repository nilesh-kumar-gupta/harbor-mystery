"""The game master agent: one player line in, narration out, at most 3 tool calls.

It sees the player's line and a compact state summary, never the case file.
After its loop, the engine's end_turn step always runs.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field

from harbor.agents import prompts
from harbor.agents.loop import run_loop
from harbor.commands import render
from harbor.llm.base import LLMClient
from harbor.tools import REGISTRY, Context
from harbor.tools.gm_tools import state_summary

MAX_TOOL_CALLS = 3
CAP_LINE = "(Too much at once. Try one thing at a time.)"


@dataclass
class TurnResult:
    narration: str
    tool_results: list = field(default_factory=list)
    status: str = "active"
    end: dict = field(default_factory=dict)   # what end_turn returned


class GameMaster:
    def __init__(self, llm: LLMClient) -> None:
        self.llm = llm
        self.system = prompts.load("game_master")

    def run_turn(self, ctx: Context, line: str) -> TurnResult:
        turn = ctx.begin_turn()
        attrs = {"gen_ai.operation.name": "invoke_agent", "gen_ai.agent.name": "game_master",
                 "game.turn": turn}
        with ctx.tracer.span("agent", "game_master", input=line, attrs=attrs) as span:
            summary = json.dumps(state_summary(ctx))
            out = run_loop(
                self.llm, ctx.tracer, "game_master", self.system,
                [{"role": "user", "content": f"Player: {line}\n\nState: {summary}"}],
                REGISTRY.schemas("gm"),
                lambda name, args: REGISTRY.call(ctx, name, args, "gm"),
                MAX_TOOL_CALLS, attrs={"game.turn": turn},
            )
            narration = out.text.strip()
            if out.capped:
                narration = CAP_LINE
            elif not narration:  # no words from the model: show the tool results plainly
                narration = "\n".join(render(n, r) for n, _, r in out.tool_results)
            end = REGISTRY.call(ctx, "end_turn", {}, "engine")
            span.output = {"narration": narration, "capped": out.capped}
        return TurnResult(narration, out.tool_results, end.get("status", "active"), end)
