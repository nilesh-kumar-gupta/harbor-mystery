"""Command-line entry point: `harbor check-case` and `harbor play`.

Holds no game rules: it creates a run, reads lines, calls tools through the
registry and prints what comes back.
"""

from __future__ import annotations

import argparse
import subprocess
import sys
import time
from pathlib import Path

from harbor.commands import HELP, parse_command, render
from harbor.engine import state
from harbor.engine.case_loader import CaseError, load_case
from harbor.tools import REGISTRY, Context
from harbor.tracing import Tracer

DEFAULT_CASE = Path("cases/saltmere.json")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="harbor", description="Harbor Mystery")
    sub = parser.add_subparsers(dest="command", required=True)
    check = sub.add_parser("check-case", help="load and validate a case file")
    check.add_argument("path", nargs="?", type=Path, default=DEFAULT_CASE)
    play = sub.add_parser("play", help="play in the terminal by typing commands")
    play.add_argument("--case", type=Path, default=DEFAULT_CASE)
    play.add_argument("--seed", type=int, default=0)
    play.add_argument("--db", default=None,
                      help="run database path (default runs/<run_id>.sqlite; "
                           "':memory:' keeps nothing)")
    args = parser.parse_args(argv)

    try:
        case = load_case(args.path if args.command == "check-case" else args.case)
    except (OSError, CaseError) as e:
        print(e, file=sys.stderr)
        return 1
    if args.command == "check-case":
        print(
            f"{case.title} ({case.case_id}): {len(case.places)} places, "
            f"{len(case.characters)} characters, {len(case.clues)} clues. OK"
        )
        return 0
    return play_loop(case, args.seed, args.db)


def play_loop(case, seed: int, db: str | None, lines=None, out=print,
              traces_dir: Path = Path("traces")) -> int:
    """The plain game loop: one typed command is one turn. `lines` defaults to stdin."""
    run_id = time.strftime("%Y%m%d-%H%M%S") + f"-s{seed}"
    if db is None:
        Path("runs").mkdir(exist_ok=True)
        db = f"runs/{run_id}.sqlite"
    if db != ":memory:" and Path(db).exists():
        print(f"{db} already exists; choose another --db path", file=sys.stderr)
        return 1
    game = state.new_game(case, run_id, seed=seed, db_path=db, git_commit=_git_commit())
    jsonl = None if db == ":memory:" else Path(traces_dir) / f"{run_id}.jsonl"
    ctx = Context(game=game, tracer=Tracer(game.conn, run_id, jsonl))
    REGISTRY.call(ctx, "start_run", {}, "engine")
    out(f"{case.title}\n{case.premise}\n{HELP}")
    for line in (lines if lines is not None else _stdin_lines()):
        line = line.strip()
        if not line:
            continue
        if line.lower() in ("quit", "exit", "q"):
            break
        if line.lower() in ("help", "?"):
            out(HELP)
            continue
        command = parse_command(line)
        if command is None:  # a typo is not a turn: no time, no stuck counter
            out("(not a command) " + HELP)
            continue
        ctx.begin_turn()
        out(render(command[0], REGISTRY.call(ctx, command[0], command[1], "gm")))
        closing = REGISTRY.call(ctx, "end_turn", {}, "engine")
        if closing.get("status") == "ended":
            out(f"The day is over ({closing.get('result')}).")
            break
    return 0


def _git_commit() -> str | None:
    """The checked-out commit, for the runs record; None outside a git checkout."""
    try:
        out = subprocess.run(["git", "rev-parse", "HEAD"], capture_output=True, text=True,
                             cwd=Path(__file__).parent, timeout=5)
    except (OSError, subprocess.SubprocessError):
        return None
    return out.stdout.strip() or None if out.returncode == 0 else None


def _stdin_lines():
    while True:
        try:
            yield input("> ")
        except EOFError:
            return


if __name__ == "__main__":
    sys.exit(main())
