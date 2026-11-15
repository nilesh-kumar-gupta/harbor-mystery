"""A case solver over the clue graph (the brief's "Case validity" check).

It works on the loaded Case, before any game exists, and answers two questions:
1. Which clues can a player reach from the start? Places by exits; characters by
   place; clues by sight, or through a character whose unlock can be met (trust
   can rise one step for a polite repeat and one per related clue the player can
   hold, up to 2; or every `unlock_if_shown` clue is reachable).
2. Do the reachable winning clues point to exactly one person? A clue points to
   the characters its text names; the suspect is whoever is named by at least
   `min_supporting` reachable winning clues, and there must be exactly one.
"""

from __future__ import annotations

import re
from collections import deque
from dataclasses import dataclass

from harbor.engine.case_loader import MAX_TRUST, Case


@dataclass
class SolverReport:
    reachable_places: set[str]
    reachable_clues: set[str]
    unreachable_required: list[str]
    suspects: list[str]           # characters named by enough reachable winning clues
    problems: list[str]

    @property
    def ok(self) -> bool:
        return not self.problems


def reachable_places(case: Case) -> set[str]:
    seen, queue = {case.start_place}, deque([case.start_place])
    while queue:
        for nxt in case.neighbors(queue.popleft()):
            if nxt not in seen:
                seen.add(nxt)
                queue.append(nxt)
    return seen


def reachable_clues(case: Case) -> set[str]:
    places = reachable_places(case)
    people = {c.id for c in case.characters if c.start_place in places}
    clues = {c.id for c in case.clues if c.found_at_place in places}
    changed = True
    while changed:                      # grow to a fixed point
        changed = False
        for k in case.knowledge:
            if k.clue_id in clues or k.character_id not in people:
                continue
            trust = min(MAX_TRUST, 1 + sum(1 for r in k.related_clues if r in clues))
            shown = bool(k.unlock_if_shown) and set(k.unlock_if_shown) <= clues
            if trust >= k.min_trust or shown:
                clues.add(k.clue_id)
                changed = True
    return clues


def names_in(case: Case, text: str) -> set[str]:
    """Characters a clue text names, by first name or surname."""
    named = set()
    for c in case.characters:
        for part in c.name.split():
            if re.search(rf"\b{re.escape(part)}\b", text):
                named.add(c.id)
    return named


def solve(case: Case) -> SolverReport:
    sol = case.solution
    places = reachable_places(case)
    clues = reachable_clues(case)
    problems = []
    unreachable = [c for c in sol.required_clue_ids if c not in clues]
    winnable = len(sol.required_clue_ids) - len(unreachable)
    if winnable < sol.min_supporting:
        problems.append(f"only {winnable} winning clues are reachable; "
                        f"{sol.min_supporting} are needed")
    counts: dict[str, int] = {}
    text = {c.id: c.text for c in case.clues}
    for clue_id in sol.required_clue_ids:
        if clue_id in clues:
            for person in names_in(case, text[clue_id]):
                counts[person] = counts.get(person, 0) + 1
    suspects = sorted(p for p, n in counts.items() if n >= sol.min_supporting)
    if suspects != [sol.culprit_id]:
        problems.append(f"the winning clues point to {suspects or 'nobody'}, "
                        f"not exactly to {sol.culprit_id!r}")
    return SolverReport(places, clues, unreachable, suspects, problems)
