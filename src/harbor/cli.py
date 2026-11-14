"""Command-line entry point. M0 only offers `harbor check-case`; the game loop arrives in M1."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from harbor.engine.case_loader import CaseError, load_case

DEFAULT_CASE = Path("cases/saltmere.json")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="harbor", description="Harbor Mystery")
    sub = parser.add_subparsers(dest="command", required=True)
    check = sub.add_parser("check-case", help="load and validate a case file")
    check.add_argument("path", nargs="?", type=Path, default=DEFAULT_CASE)
    args = parser.parse_args(argv)

    try:
        case = load_case(args.path)
    except (OSError, CaseError) as e:
        print(e, file=sys.stderr)
        return 1
    print(
        f"{case.title} ({case.case_id}): {len(case.places)} places, "
        f"{len(case.characters)} characters, {len(case.clues)} clues. OK"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
