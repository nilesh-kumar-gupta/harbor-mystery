"""Wiring for one game: the run database, the tracer, the agents and their models.

Used by the CLI, the bot and the tests, so they all play the same way.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from harbor import prompts
from harbor.agents.game_master import GameMaster, TurnResult
from harbor.engine import state
from harbor.engine.case_loader import Case
from harbor.tools import REGISTRY, Context
from harbor.tracing import Tracer


@dataclass
class Session:
    ctx: Context
    gm: GameMaster | None

    @classmethod
    def create(cls, case: Case, run_id: str, llms: dict, seed: int = 0,
               db_path: str = ":memory:", jsonl_path: str | Path | None = None,
               git_commit: str | None = None) -> "Session":
        game = state.new_game(case, run_id, seed=seed, db_path=db_path,
                              git_commit=git_commit, prompt_hash=prompts.prompt_hash(),
                              model=getattr(llms["gm"], "model", None))
        ctx = Context(game=game, tracer=Tracer(game.conn, run_id, jsonl_path))
        REGISTRY.call(ctx, "start_run", {}, "engine")
        return cls(ctx=ctx, gm=GameMaster(llms["gm"], llms["npc"], llms.get("referee")))

    def close(self):
        """Release persistent ownership and close this session's database."""
        from harbor.tools.session_tools import release_lease
        if self.ctx.tracer is not None:
            self.ctx.tracer.close_open()
        if self.ctx.game is not None:
            self.ctx.game.conn.close()
        release_lease(self.ctx)

    def turn(self, line: str) -> TurnResult:
        return self.gm.run_turn(self.ctx, line)

    @classmethod
    def open(cls, run_id: str | None = None, llms: dict | None = None) -> "Session":
        """Open a persistent run through the same recovery tool as MCP."""
        ctx = Context(require_turn=True)
        result = REGISTRY.call(ctx, "resume_game", {"run_id": run_id} if run_id else {}, "gm")
        if not result["ok"]:
            raise ValueError(result["error"])
        gm = GameMaster(llms["gm"], llms["npc"], llms.get("referee")) if llms else None
        return cls(ctx, gm)
