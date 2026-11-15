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

from harbor.commands import HELP, parse_command, render_ending
from harbor.engine.case_loader import CaseError, load_case
from harbor.offline import offline_llms
from harbor.session import Session

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
    bot = sub.add_parser("bot", help="watch the scripted bot play the case offline")
    bot.add_argument("--case", type=Path, default=DEFAULT_CASE)
    bot.add_argument("--seed", type=int, default=0)
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
    if args.command == "bot":
        return run_bot(case, args.seed)
    return play_loop(case, args.seed, args.db)


def run_bot(case, seed: int, out=print) -> int:
    """The bot plays an in-memory offline game; prints the transcript and the result."""
    from harbor.bot import Bot
    session = Session.create(case, f"bot-s{seed}", offline_llms(), seed=seed)
    game = Bot(session).play()
    for line, narration in game.transcript:
        out(f"> {line}\n{narration}")
    out(f"\nBot finished in {game.turns} turns: {game.result}")
    return 0 if game.result == "win" else 1


def play_loop(case, seed: int, db: str | None, lines=None, out=print,
              traces_dir: Path = Path("traces"), llms=None) -> int:
    """The game loop: one line is one turn for the game master. `lines` defaults to stdin.

    The game master and characters are the offline stand-ins (deterministic
    `ScriptedLLM` policies that understand typed commands) unless `llms` is given.
    """
    if llms is None:
        llms = offline_llms()
    run_id = time.strftime("%Y%m%d-%H%M%S") + f"-s{seed}"
    if db is None:
        Path("runs").mkdir(exist_ok=True)
        db = f"runs/{run_id}.sqlite"
    jsonl = None if db == ":memory:" else Path(traces_dir) / f"{run_id}.jsonl"
    for path in (Path(db) if db != ":memory:" else None, jsonl):
        if path is not None and path.exists():
            print(f"{path} already exists; try again or choose another --db path",
                  file=sys.stderr)
            return 1
    session = Session.create(case, run_id, llms, seed=seed, db_path=db, jsonl_path=jsonl,
                             git_commit=_git_commit())
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
        if parse_command(line) is None:  # a typo is not a turn
            out("(not a command) " + HELP)
            continue
        result = session.turn(line)
        out(result.narration)
        if result.status == "ended":
            if result.end.get("result") == "timeout":
                out(f"The day is over (timeout).\n{render_ending(result.end)}")
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
    echo = not sys.stdin.isatty()   # commands piped from a file: show them, as a transcript
    while True:
        try:
            line = input("> ")
        except EOFError:
            return
        if echo:
            print(line)
        yield line


if __name__ == "__main__":
    sys.exit(main())
