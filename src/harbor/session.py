"""Wiring for one game: the run database, the tracer, the agents and their models.

Used by the CLI, the bot and the tests, so they all play the same way.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from harbor import prompts
from harbor.agents.game_master import GameMaster, TurnResult
from harbor.agents.npc import make_npc_runner
from harbor.engine import state
from harbor.engine.case_loader import Case
from harbor.tools import REGISTRY, Context
from harbor.tracing import Tracer


def hosted_llms() -> dict:
    from harbor.llm.hosted import HostedLLM
    model = HostedLLM()
    return {"gm": model, "npc": model, "referee": model}


@dataclass
class Session:
    ctx: Context
    gm: GameMaster

    @classmethod
    def create(cls, case: Case, run_id: str, llms: dict, seed: int = 0,
               db_path: str = ":memory:", jsonl_path: str | Path | None = None,
               git_commit: str | None = None) -> "Session":
        game = state.new_game(case, run_id, seed=seed, db_path=db_path,
                              git_commit=git_commit, prompt_hash=prompts.prompt_hash(),
                              model=getattr(llms["gm"], "model", None))
        ctx = Context(game=game, tracer=Tracer(game.conn, run_id, jsonl_path),
                      npc_runner=make_npc_runner(llms["npc"]),
                      referee_llm=llms.get("referee"))
        REGISTRY.call(ctx, "start_run", {}, "engine")
        return cls(ctx=ctx, gm=GameMaster(llms["gm"]))

    def turn(self, line: str) -> TurnResult:
        return self.gm.run_turn(self.ctx, line)
