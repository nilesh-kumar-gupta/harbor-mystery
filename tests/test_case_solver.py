"""Case validity: every clue needed for a win is reachable from the start, and
the clues point to exactly one culprit."""

import copy
import json

from harbor.engine.case_loader import parse_case
from harbor.solver import reachable_clues, solve

from conftest import CASE_PATH

RAW = json.loads(CASE_PATH.read_text(encoding="utf-8"))


def test_saltmere_is_solvable_and_points_to_one_culprit(case):
    report = solve(case)
    assert report.ok, report.problems
    assert report.unreachable_required == []
    assert report.suspects == ["tom"]          # Hale (C7, C8) is a red herring and an alibi
    assert report.unnamed == ["C1", "C3", "C4"]
    assert report.reachable_places == {p.id for p in case.places}
    assert report.reachable_clues == {c.id for c in case.clues}


def mutated(change):
    data = copy.deepcopy(RAW)
    change(data)
    return parse_case(data)


def test_a_guarded_clue_with_no_way_to_earn_trust_is_unreachable():
    def guard(d):
        for k in d["knowledge"]:
            if k["clue_id"] == "C9":
                k.update(related_clues=[], unlock_if_shown=[])
    assert "C9" not in reachable_clues(mutated(guard))


def test_a_cut_off_place_makes_its_clues_unreachable():
    def cut(d):
        d["exits"] = [e for e in d["exits"] if "lighthouse" not in e]  # nothing leads there
    report = solve(mutated(cut))
    assert "C4" in report.unreachable_required and report.ok      # 4 of 5 still reachable


def test_too_few_reachable_clues_is_a_problem():
    def strip(d):
        d["knowledge"] = [k for k in d["knowledge"] if k["clue_id"] not in ("C2", "C5", "C6", "C4")]
    report = solve(mutated(strip))
    assert not report.ok and "only 1 winning clues are reachable" in report.problems[0]


def test_clues_pointing_at_two_people_is_a_problem():
    def blame_hale(d):
        for c in d["clues"]:
            if c["id"] in ("C3", "C4"):
                c["text"] += " Hale Pruitt was seen nearby."
    report = solve(mutated(blame_hale))
    assert not report.ok and report.suspects == ["hale", "tom"]


def test_evidence_outside_the_winning_list_still_counts():
    """The red herring is excluded by its kind, not by the solution's own list."""
    def herring_as_motive(d):
        for c in d["clues"]:
            if c["id"] in ("C7", "C8"):
                c["kind"] = "motive"
    report = solve(mutated(herring_as_motive))
    assert not report.ok and report.suspects == ["hale", "tom"]


def test_the_wrong_culprit_in_the_solution_is_a_problem():
    report = solve(mutated(lambda d: d["solution"].update(culprit_id="hale")))
    assert not report.ok and "not exactly to 'hale'" in report.problems[0]
