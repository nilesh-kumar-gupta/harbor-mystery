"""Every case in cases/*.json passes the checks that define a valid case (ADR 005).

Each test runs once per case file, so a new case is checked by dropping it into
`cases/`. The checks: it loads; the solver reaches every clue and finds exactly one
culprit (by the hand-written `implicates` labels, ignoring topics); the scripted bot
wins it; and its keywords work with the referee's code check for the fixed lines a
case supplies (fallback lines, pointer lines, clue texts). They cannot judge how a
real model will paraphrase; that stays with the referee's model check and review.
"""

import json
import re
from pathlib import Path

import pytest

from harbor import referee
from harbor.bot import Bot
from harbor.engine import rules, state
from harbor.engine.case_loader import load_case
from harbor.offline import offline_llms
from harbor.session import Session
from harbor.solver import solve
from harbor.tools import REGISTRY, Context

CASE_PATHS = sorted((Path(__file__).parent.parent / "cases").glob("*.json"))
CASES = [load_case(p) for p in CASE_PATHS]
IDS = [c.case_id for c in CASES]
BOT_TURN_LIMIT = 40          # as in test_bot_player: the bot must win in under 40 turns

# Clue keywords in text every character's prompt can draw on (premise, place
# descriptions), so a natural reply may trip them. Listed when deliberate.
INTENDED_SHARED_KEYWORDS = {
    # Saltmere's predate this check; its wording rewrite may remove them.
    "saltmere": {("premise", "C1", "pins"), ("fish_market", "C4", "skiff")},
}

# Clue keywords that occur in a character's own persona or speech style on purpose.
# The referee ignores such a keyword for that character (ADR 002 #25), so each one
# is a deliberate hole in the code check and must be listed here.
INTENDED_PERSONA_KEYWORDS = {
    "saltmere": {("tom", "knee"), ("hale", "fees")},
    "gaiety": set(),
    "kestrel": set(),
}

per_case = pytest.mark.parametrize("case", CASES, ids=IDS)


def fresh_game(case):
    """A started in-memory run of `case`, nothing found, nobody spoken to."""
    ctx = Context(game=state.new_game(case, f"all-cases-{case.case_id}", seed=1))
    assert REGISTRY.call(ctx, "start_run", {}, "engine")["ok"]
    return ctx.game


def keywords_in(text, case):
    """(clue id, keyword) for every clue keyword that occurs in `text`."""
    return {(c.id, k) for c in case.clues for k in rules.matches(text, c.keywords)}


# ---- loading and the solver ---------------------------------------------------

@pytest.mark.parametrize("path", CASE_PATHS, ids=[p.stem for p in CASE_PATHS])
def test_the_case_loads_and_passes_check_case(path, capsys):
    from harbor.cli import main
    assert load_case(path).case_id == path.stem          # cases/<case_id>.json
    assert main(["check-case", str(path)]) == 0
    assert capsys.readouterr().out.rstrip().endswith("OK")


@per_case
def test_every_clue_is_reachable_and_the_evidence_names_one_culprit(case):
    report = solve(case)
    assert report.ok, report.problems
    assert report.unreachable_required == []
    assert report.suspects == [case.solution.culprit_id]
    assert report.reachable_places == {p.id for p in case.places}
    assert report.reachable_clues == {c.id for c in case.clues}


@per_case
def test_implicates_agree_with_the_solution(case):
    culprit = case.solution.culprit_id
    clues = {c.id: c for c in case.clues}
    for cid in case.solution.required_clue_ids:
        assert culprit in clues[cid].implicates, cid
        assert clues[cid].kind not in ("red_herring", "alibi"), cid
    for c in case.clues:
        if c.kind == "red_herring":
            assert c.implicates and culprit not in c.implicates, c.id
        if c.kind == "alibi":
            assert c.implicates == (), c.id


@per_case
def test_nudges_and_pointers_lead_to_people_who_know(case):
    knows = {(k.character_id, k.clue_id) for k in case.knowledge}
    knowers = {cl for _, cl in knows}
    kinds = {c.id: c.kind for c in case.clues}
    for cid in case.nudge_order:          # a look-only clue would arm a nudge nobody can give
        assert cid in knowers, cid
        assert kinds[cid] not in ("red_herring", "alibi"), cid
    for p in case.pointers:
        assert (p.points_to_character, p.clue_id) in knows, p


# ---- the bot --------------------------------------------------------------------

@per_case
def test_the_bot_wins(case):
    session = Session.create(case, f"bot-{case.case_id}", offline_llms(), seed=1)
    game = Bot(session).play()
    assert game.result == "win", game.transcript[-1]
    assert game.turns < BOT_TURN_LIMIT
    culprit = next(c.name for c in case.characters if c.id == case.solution.culprit_id)
    assert game.accusation == culprit
    ended = session.ctx.game.conn.execute(
        "SELECT payload FROM events WHERE type = 'game_ended'").fetchone()["payload"]
    cited = set(json.loads(ended)["clue_ids"])
    assert len(cited & set(case.solution.required_clue_ids)) >= case.solution.min_supporting
    assert cited <= state.found_clue_ids(session.ctx.game)


# ---- the referee's keyword code check ---------------------------------------------

@per_case
def test_fallback_lines_never_trip_the_code_check(case):
    game = fresh_game(case)
    for c in case.characters:
        verdict = referee.code_check(game, c.id, "", c.fallback_line)
        assert verdict.ok, (c.id, verdict.reason)


@per_case
def test_pointer_lines_never_trip_the_code_check(case):
    """The referee strips a character's pointer lines only when quoted exactly, so
    exact lines always pass. Altered punctuation defeats the strip: then the line may
    trip only the clue it points to (Saltmere's Bram names the cutters he points to)."""
    game = fresh_game(case)
    for p in case.pointers:
        assert referee.code_check(game, p.character_id, "", p.line).ok
        altered = p.line.replace(".", ";").replace("!", ";").replace(",", ";")
        assert altered != p.line, p.line
        leaked = referee.code_check(game, p.character_id, "", altered).leaked
        assert set(leaked) <= {p.clue_id}, (p.character_id, p.clue_id, leaked)


@per_case
def test_the_engines_nudge_line_never_trips_the_code_check(case):
    """A nudge pointer has no line; the offline stand-in says "Ask <name>.", so no
    keyword may match a character's name."""
    game = fresh_game(case)
    for speaker in case.characters:
        for other in case.characters:
            if other.id != speaker.id:
                assert referee.code_check(game, speaker.id, "", f"Ask {other.name}.").ok


@per_case
def test_no_keyword_matches_a_name_or_place(case):
    names = [c.name for c in case.characters] + [p.name for p in case.places]
    for name in names:
        assert not keywords_in(name, case), name


@per_case
def test_stating_a_clue_trips_exactly_that_clue(case):
    """Said by anyone before it is revealed, a clue's text is caught as a leak of that
    clue (no missed leak) and of no other clue (no false leak once it is revealed)."""
    game = fresh_game(case)
    for clue in case.clues:
        for c in case.characters:
            verdict = referee.code_check(game, c.id, "", clue.text)
            assert verdict.leaked == [clue.id], (c.id, clue.id, verdict.leaked)


@per_case
def test_keywords_occur_in_their_own_clue(case):
    """A player who quotes a notebook clue shows it (ADR 001 #3)."""
    for clue in case.clues:
        assert clue.keywords and rules.matches(clue.text, clue.keywords), clue.id


@per_case
def test_premise_and_place_descriptions_carry_only_intended_keywords(case):
    found = {("premise", cid, k) for cid, k in keywords_in(case.premise, case)}
    for place in case.places:
        found |= {(place.id, cid, k) for cid, k in keywords_in(place.description, case)}
    assert found == INTENDED_SHARED_KEYWORDS.get(case.case_id, set())


@per_case
def test_persona_keywords_are_only_the_intended_ones(case):
    found = set()
    for c in case.characters:
        for _, k in keywords_in(f"{c.persona} {c.speech_style}", case):
            found.add((c.id, k))
    assert found == INTENDED_PERSONA_KEYWORDS.get(case.case_id, set())


# ---- characters and names -------------------------------------------------------------

@per_case
def test_characters_are_distinct(case):
    chars = case.characters
    for field in ("persona", "speech_style", "fallback_line", "role"):
        values = [getattr(c, field) for c in chars]
        assert len(set(values)) == len(values), field
    parts = [part for c in chars for part in c.name.split()]
    assert len(set(parts)) == len(parts)           # no shared first names or surnames


@per_case
def test_names_work_with_the_typed_commands(case):
    """Names end up in "Here: A, B." and "Exits: X, Y." lines and in `talk <name>: ...`."""
    for name in [c.name for c in case.characters] + [p.name for p in case.places]:
        assert not re.search(r"[.,:\[\]]", name), name
