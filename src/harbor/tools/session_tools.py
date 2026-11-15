"""Registered session tools; select the database before entering the tool guard."""
import os
import random
import uuid
from pathlib import Path

from harbor.commands import HELP
from harbor.engine import interaction, schema, state
from harbor.engine.case_loader import load_case
from harbor.tools.registry import REGISTRY, error
from harbor.tools.gm_tools import state_summary
from harbor.tracing import Tracer


def home():
    return Path(os.environ.get("HARBOR_HOME", ".")).resolve()


def open_game(path):
    conn = schema.connect(str(path))
    try:
        row = conn.execute("SELECT run_id, seed FROM runs").fetchone()
        game = state.Game(conn, row["run_id"], random.Random(row["seed"]))
        # Advance the deterministic generator for ids already allocated.
        for _ in conn.execute("SELECT interaction_id FROM interactions"):
            game.rng.getrandbits(16)
        state.read(game)
        return game
    except Exception:
        conn.close()
        raise


def prepare(ctx, name, args):
    if name == "game_status":
        if ctx.game is None:
            return {"ok": True, "loaded": False}
        return None
    if name == "new_game":
        if ctx.game is not None and not state.is_over(ctx.game):
            return error("a game is already active")
        folder = Path(os.environ.get("HARBOR_CASES", Path(__file__).resolve().parents[3] / "cases"))
        available = sorted(p.stem for p in folder.glob("*.json"))
        case_id = args.get("case", "saltmere")
        if case_id not in available:
            return error(f"unknown case {case_id!r}; available: {', '.join(available)}", available=available)
        case = load_case(folder / f"{case_id}.json")
        run_id = uuid.uuid4().hex
        folder = home() / "runs"
        folder.mkdir(parents=True, exist_ok=True)
        game = state.new_game(case, run_id, args.get("seed", 0), str(folder / f"{run_id}.sqlite"))
    else:
        folder = home() / "runs"
        run_id = args.get("run_id")
        if run_id is not None:
            if not run_id or Path(run_id).name != run_id or run_id in (".", ".."):
                return error("invalid run id")
            path = folder / f"{run_id}.sqlite"
        else:
            paths = list(folder.glob("*.sqlite"))
            if not paths:
                return error("no saved runs")
            path = max(paths, key=lambda p: p.stat().st_mtime_ns)
        if not path.is_file():
            return error("unknown run")
        game = open_game(path)
    tracer = Tracer(game.conn, game.run_id, home() / "traces" / f"{game.run_id}.jsonl")
    if ctx.tracer is not None:
        ctx.tracer._virtual.clear()
    same_run = ctx.game is not None and ctx.game.run_id == game.run_id
    ctx.game, ctx.tracer = game, tracer
    game.tracer = tracer
    if not same_run:
        ctx.counts.clear()
        ctx.limited.clear()
    ctx.turn_open = False
    ctx.trace_warning_seen = 0


@REGISTRY.tool("new_game", "gm", "Start a new game.",
               {"seed": {"type": "integer"}, "case": {"type": "string"}}, session=True)
def new_game(ctx, seed=0, case="saltmere"):
    state.start(ctx.game)
    meta = state.case_meta(ctx.game)
    return {"ok": True, "run_id": ctx.game.run_id, "title": meta["title"],
            "premise": meta["premise"], "help": HELP, **state_summary(ctx)}


@REGISTRY.tool("game_status", "gm", "Inspect the loaded session.", session=True, when_ended=True)
def game_status(ctx):
    game = ctx.game
    return {"ok": True, "loaded": True, "run_id": game.run_id,
            "case_id": state.case_meta(game)["case_id"], "turn": state.read(game)["turn"],
            "open_interaction": dict(interaction.opened(game)) if interaction.opened(game) else None,
            "db_path": game.conn.execute("PRAGMA database_list").fetchone()[2],
            "trace_path": str(ctx.tracer.jsonl_path) if ctx.tracer.jsonl_path else None,
            "trace_failures": ctx.tracer.trace_failures}


@REGISTRY.tool("resume_game", "gm", "Open a saved run and recover interrupted conversations.",
               {"run_id": {"type": "string"}}, session=True, when_ended=True)
def resume_game(ctx, run_id=None):
    game = ctx.game
    current = interaction.opened(game)
    recovered = interaction.recover(game)
    # Reconstruct only absent durable virtual spans, including earlier attempts.
    for row in game.conn.execute("SELECT * FROM interactions"):
        trace = f"{game.run_id}-t{row['turn']:04d}"
        spans = [(f"{trace}-gm", "agent", "game_master", None, {})]
        for attempt in game.conn.execute("SELECT * FROM attempts WHERE interaction_id=?", (row["interaction_id"],)):
            attrs = {"game.interaction_id": row["interaction_id"], "game.attempt": attempt["attempt"]}
            for suffix, kind, name in (("npc", "agent", f"npc:{row['character_id']}"), ("ref", "referee", "referee")):
                spans.append((f"{row['interaction_id']}-a{attempt['attempt']}-{suffix}", kind, name, f"{trace}-gm", attrs))
        for sid, kind, name, parent, attrs in spans:
            if game.conn.execute("SELECT 1 FROM spans WHERE span_id=?", (sid,)).fetchone():
                continue
            span = ctx.tracer.open_virtual(kind, name, sid, parent_id=parent, trace_id=trace,
                attrs={**attrs, "harbor.reconstructed": True, "harbor.model_metadata": "unavailable",
                       "harbor.start_time": "unavailable"})
            ctx.tracer.close_virtual(span.span_id, status="interrupted",
                                     output=recovered if current and row["interaction_id"] == current["interaction_id"] else None)
    return {"ok": True, "run_id": game.run_id, "recovered": [recovered] if recovered else [],
            "summary": state_summary(ctx)}
