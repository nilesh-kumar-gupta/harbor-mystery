import copy
import json
from pathlib import Path

import pytest

from harbor.cli import main
from harbor.engine.case_loader import CaseError, load_case, parse_case

CASE_PATH = Path(__file__).parent.parent / "cases" / "saltmere.json"


@pytest.fixture
def raw():
    return json.loads(CASE_PATH.read_text(encoding="utf-8"))


def test_saltmere_loads():
    case = load_case(CASE_PATH)
    assert case.case_id == "saltmere"
    assert len(case.places) == 6
    assert len(case.characters) == 6
    assert [c.id for c in case.clues] == [f"C{i}" for i in range(1, 10)]
    assert case.start_place == "quay"
    assert (case.start_clock, case.end_clock) == ("06:00", "18:00")
    assert case.solution.culprit_id == "tom"
    assert case.solution.required_clue_ids == ("C2", "C3", "C4", "C5", "C6")
    assert case.nudge_order == ("C2", "C3", "C4", "C5", "C6")


def test_exits_work_in_both_directions():
    case = load_case(CASE_PATH)
    assert case.neighbors("quay") == ["office", "fish_market", "chandlery"]
    assert case.neighbors("lighthouse") == ["chandlery"]
    assert "quay" in case.neighbors("chandlery")


def test_brief_knowledge_assignments():
    case = load_case(CASE_PATH)
    knows = {(k.character_id, k.clue_id): k for k in case.knowledge}
    assert knows[("ysolde", "C5")].min_trust == 1
    assert knows[("ysolde", "C5")].related_clues == ("C1",)
    assert knows[("tom", "C9")].unlock_if_shown == ("C3", "C5")
    assert ("tom", "C5") not in knows
    visible = [c.id for c in case.clues if c.found_at_place]
    assert visible == ["C1"]


@pytest.mark.parametrize(
    "mutate, expected",
    [
        (lambda d: d["exits"].append(["quay", "moon"]), "unknown id 'moon'"),
        (lambda d: d["clues"][0].update(kind="rumour"), "unknown kind 'rumour'"),
        (lambda d: d["places"].append(dict(d["places"][0])), "duplicate place id 'quay'"),
        (lambda d: d["knowledge"][0].update(clue_id="C99"), "unknown id 'C99'"),
        (lambda d: d["knowledge"][0].update(min_trust=5), "min_trust must be 0..2"),
        (lambda d: d["solution"].update(culprit_id="nobody"), "unknown id 'nobody'"),
        (lambda d: d["characters"][0].update(fallback_line=" "), "fallback_line is empty"),
        (lambda d: d.update(start_clock="6am"), "is not HH:MM"),
        (lambda d: d.pop("solution"), "missing or malformed field"),
        (lambda d: d["clues"][1].update(implicates=["nobody"]), "C2 implicates: unknown id 'nobody'"),
        (lambda d: d["clues"][1].pop("implicates"), "C2: implicates must be a list"),
        (lambda d: d["clues"][1].update(implicates="tom"), "C2: implicates must be a list"),
    ],
)
def test_invalid_cases_are_rejected(raw, mutate, expected):
    data = copy.deepcopy(raw)
    mutate(data)
    with pytest.raises(CaseError) as err:
        parse_case(data)
    assert expected in str(err.value)


def test_all_problems_are_reported_together(raw):
    raw["exits"].append(["quay", "moon"])
    raw["clues"][0]["kind"] = "rumour"
    with pytest.raises(CaseError) as err:
        parse_case(raw)
    assert len(err.value.problems) == 2


def test_check_case_command(capsys):
    assert main(["check-case", str(CASE_PATH)]) == 0
    assert "6 places, 6 characters, 9 clues. OK" in capsys.readouterr().out
    assert main(["check-case", "does/not/exist.json"]) == 1
