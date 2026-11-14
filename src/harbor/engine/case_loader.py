"""Read a case file (cases/*.json) into validated, read-only Python objects.

The loader checks every cross-reference up front and reports all problems at
once, so a broken case fails here and not halfway through a game.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path

CLUE_KINDS = ("motive", "method", "opportunity", "red_herring", "alibi")
MAX_TRUST = 2
_CLOCK = re.compile(r"^([01]\d|2[0-3]):[0-5]\d$")


class CaseError(ValueError):
    """The case file is malformed. `problems` lists every issue found."""

    def __init__(self, problems: list[str]):
        self.problems = problems
        super().__init__("invalid case file:\n  - " + "\n  - ".join(problems))


@dataclass(frozen=True)
class Place:
    id: str
    name: str
    description: str


@dataclass(frozen=True)
class Character:
    id: str
    name: str
    role: str
    persona: str
    speech_style: str
    start_place: str
    schedule: tuple
    fallback_line: str


@dataclass(frozen=True)
class Clue:
    id: str
    text: str
    kind: str
    found_at_place: str | None
    keywords: tuple[str, ...]


@dataclass(frozen=True)
class Knowledge:
    character_id: str
    clue_id: str
    unlock_topics: tuple[str, ...]  # empty: any message unlocks it
    min_trust: int
    related_clues: tuple[str, ...]  # showing one of these can raise trust
    unlock_if_shown: tuple[str, ...]  # showing all of these unlocks it at any trust


@dataclass(frozen=True)
class Pointer:
    character_id: str
    clue_id: str
    points_to_character: str
    misleading: bool
    line: str


@dataclass(frozen=True)
class Solution:
    culprit_id: str
    motive: str
    method: str
    required_clue_ids: tuple[str, ...]
    min_supporting: int
    true_timeline: tuple[str, ...]


@dataclass(frozen=True)
class Case:
    case_id: str
    title: str
    premise: str
    start_place: str
    start_clock: str
    end_clock: str
    places: tuple[Place, ...]
    exits: tuple[tuple[str, str], ...]  # undirected pairs
    characters: tuple[Character, ...]
    clues: tuple[Clue, ...]
    knowledge: tuple[Knowledge, ...]
    pointers: tuple[Pointer, ...]
    solution: Solution
    nudge_order: tuple[str, ...]

    def neighbors(self, place_id: str) -> list[str]:
        """Places reachable in one move, in case-file order."""
        out = []
        for a, b in self.exits:
            if a == place_id:
                out.append(b)
            elif b == place_id:
                out.append(a)
        return out


def load_case(path: str | Path) -> Case:
    with open(path, encoding="utf-8") as f:
        return parse_case(json.load(f))


def parse_case(data: dict) -> Case:
    """Build a Case from a decoded JSON object, or raise CaseError."""
    try:
        case = _build(data)
    except (KeyError, TypeError, ValueError) as e:
        raise CaseError([f"missing or malformed field: {e!r}"]) from e
    problems = _validate(case)
    if problems:
        raise CaseError(problems)
    return case


def _build(d: dict) -> Case:
    s = d["solution"]
    return Case(
        case_id=d["case_id"],
        title=d["title"],
        premise=d["premise"],
        start_place=d["start_place"],
        start_clock=d["start_clock"],
        end_clock=d["end_clock"],
        places=tuple(Place(p["id"], p["name"], p["description"]) for p in d["places"]),
        exits=tuple((a, b) for a, b in d["exits"]),
        characters=tuple(
            Character(
                c["id"], c["name"], c["role"], c["persona"], c["speech_style"],
                c["start_place"], tuple(c.get("schedule", ())), c["fallback_line"],
            )
            for c in d["characters"]
        ),
        clues=tuple(
            Clue(c["id"], c["text"], c["kind"], c.get("found_at_place"),
                 tuple(c.get("keywords", ())))
            for c in d["clues"]
        ),
        knowledge=tuple(
            Knowledge(
                k["character_id"], k["clue_id"], tuple(k.get("unlock_topics", ())),
                int(k.get("min_trust", 0)), tuple(k.get("related_clues", ())),
                tuple(k.get("unlock_if_shown", ())),
            )
            for k in d["knowledge"]
        ),
        pointers=tuple(
            Pointer(p["character_id"], p["clue_id"], p["points_to_character"],
                    bool(p.get("misleading", False)), p["line"])
            for p in d["pointers"]
        ),
        solution=Solution(
            s["culprit_id"], s["motive"], s["method"], tuple(s["required_clue_ids"]),
            int(s["min_supporting"]), tuple(s["true_timeline"]),
        ),
        nudge_order=tuple(d["nudge_order"]),
    )


def _validate(case: Case) -> list[str]:
    problems: list[str] = []

    def ids(items, label):
        seen = set()
        for item in items:
            if item.id in seen:
                problems.append(f"duplicate {label} id {item.id!r}")
            seen.add(item.id)
        return seen

    places = ids(case.places, "place")
    chars = ids(case.characters, "character")
    clues = ids(case.clues, "clue")

    def need(value, valid, what):
        if value not in valid:
            problems.append(f"{what}: unknown id {value!r}")

    need(case.start_place, places, "start_place")
    for label, clock in (("start_clock", case.start_clock), ("end_clock", case.end_clock)):
        if not _CLOCK.match(clock):
            problems.append(f"{label}: {clock!r} is not HH:MM")
    if not problems and case.start_clock >= case.end_clock:
        problems.append("start_clock must be before end_clock")

    for a, b in case.exits:
        need(a, places, "exit")
        need(b, places, "exit")
        if a == b:
            problems.append(f"exit: {a!r} connects to itself")

    for c in case.characters:
        need(c.start_place, places, f"character {c.id} start_place")
        if not c.fallback_line.strip():
            problems.append(f"character {c.id}: fallback_line is empty")

    for c in case.clues:
        if c.kind not in CLUE_KINDS:
            problems.append(f"clue {c.id}: unknown kind {c.kind!r}")
        if c.found_at_place is not None:
            need(c.found_at_place, places, f"clue {c.id} found_at_place")

    seen_pairs = set()
    for k in case.knowledge:
        where = f"knowledge {k.character_id}/{k.clue_id}"
        need(k.character_id, chars, where)
        need(k.clue_id, clues, where)
        for cid in (*k.related_clues, *k.unlock_if_shown):
            need(cid, clues, where)
        if not 0 <= k.min_trust <= MAX_TRUST:
            problems.append(f"{where}: min_trust must be 0..{MAX_TRUST}")
        if (k.character_id, k.clue_id) in seen_pairs:
            problems.append(f"{where}: listed twice")
        seen_pairs.add((k.character_id, k.clue_id))

    for p in case.pointers:
        where = f"pointer {p.character_id}/{p.clue_id}"
        need(p.character_id, chars, where)
        need(p.clue_id, clues, where)
        need(p.points_to_character, chars, where)
        if p.character_id == p.points_to_character:
            problems.append(f"{where}: points to itself")

    sol = case.solution
    need(sol.culprit_id, chars, "solution culprit_id")
    for cid in sol.required_clue_ids:
        need(cid, clues, "solution required_clue_ids")
    if not 1 <= sol.min_supporting <= len(sol.required_clue_ids):
        problems.append("solution min_supporting must be between 1 and the number of required clues")
    for cid in case.nudge_order:
        need(cid, clues, "nudge_order")

    return problems
