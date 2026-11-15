"""The game master agent: one player line in, narration out, at most 3 tool calls.

It sees the player's line and a compact state summary, never the case file.
After its loop, the engine's end_turn step always runs.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field

from harbor import prompts
from harbor.agents.loop import LoopResult, run_loop
from harbor.agents import conversation
from harbor.commands import render
from harbor.llm.base import LLMClient
from harbor.tools import REGISTRY, Context
from harbor.tools.gm_tools import state_summary
from harbor.tools.registry import check_arguments, error

MAX_TOOL_CALLS = 3
CAP_LINE = "(Too much at once. Try one thing at a time.)"
MODEL_ERROR = "(The game master could not answer.)"


@dataclass
class TurnResult:
    narration: str
    tool_results: list = field(default_factory=list)
    status: str = "active"
    end: dict = field(default_factory=dict)   # what end_turn returned


class GameMaster:
    def __init__(self, llm: LLMClient, npc_llm=None, referee_llm=None) -> None:
        self.llm = llm
        self.npc_llm, self.referee_llm = npc_llm, referee_llm
        self.system = prompts.load("game_master")

    def run_turn(self, ctx: Context, line: str) -> TurnResult:
        started = REGISTRY.call(ctx, "player_turn", {"line": line}, "gm")
        if not started.get("ok"):
            status = state_summary(ctx)["status"]
            return TurnResult(started["error"], status=status, end=started)
        turn = started.get("turn", 0)
        attrs = {"gen_ai.operation.name": "invoke_agent", "gen_ai.agent.name": "game_master",
                 "game.turn": turn}
        with ctx.tracer.span("agent", "offline_turn", input=line, attrs=attrs,
                             parent_id=f"{ctx.tracer.trace_id}-gm") as span:
            summary = json.dumps(state_summary(ctx))
            try:
                out = run_loop(
                    self.llm, ctx.tracer, "game_master", self.system,
                    [{"role": "user", "content": f"Player: {line}\n\nState: {summary}"}],
                    self.schemas(),
                    lambda name, args: self.execute(ctx, name, args),
                    MAX_TOOL_CALLS, attrs={"game.turn": turn},
                )
            except Exception as e:  # e.g. the model client fails: the turn still ends
                span.status = "error"
                span.attrs["error.type"] = type(e).__name__
                out = LoopResult(text=f"{MODEL_ERROR} ({type(e).__name__}: {e})")
            narration = out.text.strip()
            if out.capped:  # the calls that ran still changed the game: show them
                narration = "\n".join([CAP_LINE] + [render(n, r) for n, _, r in out.tool_results])
            elif not narration:  # no words from the model: show the tool results plainly
                narration = "\n".join(render(n, r) for n, _, r in out.tool_results)
            gm_span = ctx.tracer._virtual.get(f"{ctx.tracer.trace_id}-gm")
            if gm_span:
                gm_span.status, gm_span.output = span.status, {"narration": narration, "capped": out.capped}
            end = REGISTRY.call(ctx, "close_turn", {}, "gm")
            span.output = {"narration": narration, "capped": out.capped}
        return TurnResult(narration, out.tool_results, end.get("status", "active"), end)

    def schemas(self):
        names = ("look", "go", "notebook", "begin_talk", "hint", "accuse")
        return [{"name": "talk" if n == "begin_talk" else n,
                 "description": ("Talk to a present character and return their validated reply. Costs 10 minutes."
                                 if n == "begin_talk" else REGISTRY.get(n).description),
                 "input_schema": REGISTRY.get(n).input_schema} for n in names]

    def execute(self, ctx, name, args):
        if name not in {s["name"] for s in self.schemas()}:
            return error(f"unknown tool {name!r}")
        if name == "talk":
            problem = check_arguments(REGISTRY.get("begin_talk").input_schema, args)
            if problem:
                return error(problem)
            return conversation.run(ctx, self.npc_llm, self.referee_llm, **args)
        return REGISTRY.call(ctx, name, args, "gm")
