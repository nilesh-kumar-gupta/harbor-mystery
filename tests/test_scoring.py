"""accuse and scoring: STRUCTURE example 7's table, the ending, and game over."""

import pytest

from harbor.engine import events, state
from harbor.tools import REGISTRY, Draft


def grant(ctx, *clue_ids):
    """Put clues in the notebook the honest way: a scripted NPC who knows them reveals them."""
    knower = {"C2": ("office", "odrin"), "C3": ("quay", "mira"), "C4": ("lighthouse", "bram"),
              "C6": ("salt_cask", "hale"), "C7": ("office", "odrin"), "C8": ("quay", "mira")}
    for clue_id in clue_ids:
        place, who = knower[clue_id]
        route = {"quay": [], "office": ["office"], "lighthouse": ["chandlery", "lighthouse"],
                 "salt_cask": ["fish market", "salt cask"]}[place]
        for p in route:
            REGISTRY.call(ctx, "go", {"place": p}, "gm")

        def runner(c, card, message, note, clue_id=clue_id):
            REGISTRY.call(c, "reveal_clue", {"clue_id": clue_id}, "npc")
            return Draft("Hm.")
        ctx.npc_runner = runner
        message = {"C2": "the town meeting?", "C3": "last night on the quay?",
                   "C4": "boats at night?", "C6": "Tom at midnight?", "C7": "fees?",
                   "C8": "the tavern?"}[clue_id]
        assert clue_id in REGISTRY.call(ctx, "talk", {"character": who, "message": message},
                                        "gm")["new_clues"]
        for p in reversed(["quay"] + route[:-1]) if route else ():   # walk back to the quay
            REGISTRY.call(ctx, "go", {"place": p}, "gm")


def accuse(ctx, who, clue_ids, motive="greed"):
    return REGISTRY.call(ctx, "accuse", {"character": who, "motive": motive,
                                         "clue_ids": clue_ids}, "gm")


@pytest.mark.parametrize("who, clues, result", [
    ("tom", ["C3", "C4"], "win"),
    ("Tom Brask", ["C2", "C3", "C4", "C6"], "win"),
    ("tom", ["C3"], "partial"),
    ("tom", ["C3", "C8"], "partial"),           # C8 is found but does not support
    ("tom", [], "partial"),
    ("hale", ["C3", "C4"], "lose"),
    ("odrin", [], "lose"),
])
def test_accusation_results(ctx, who, clues, result):
    grant(ctx, "C3", "C4", "C8", "C2", "C6")
    out = accuse(ctx, who, clues)
    assert out["ok"] and out["result"] == result
    assert out["culprit"] == "Tom Brask" and out["true_timeline"][0] == "00:00 Tom leaves the tavern."
    st = state.read(ctx.game)
    assert (st["status"], st["result"]) == ("ended", result)


def test_the_ending_lists_found_and_missed_and_the_true_motive(ctx):
    grant(ctx, "C3", "C4")
    out = accuse(ctx, "tom", ["C3", "C4", "C3"], motive="keep his monopoly")
    assert out["supporting"] == ["C3", "C4"]                       # repeats dropped
    assert out["found"] == ["C3", "C4"] and out["missed"] == ["C2", "C5", "C6"]
    assert out["true_motive"].startswith("Bigger ships") and out["motive_given"] == "keep his monopoly"
    ended = [e for e in events.log(ctx.game.conn, ctx.game.run_id) if e["type"] == "game_ended"]
    assert ended[0]["payload"] == {"result": "win", "accused": "tom",
                                   "motive": "keep his monopoly", "clue_ids": ["C3", "C4"]}


@pytest.mark.parametrize("who, clues, error", [
    ("tom", ["C3", "C5"], "clue C5 is not in the notebook"),     # guessing ids does not count
    ("tom", ["C42"], "unknown clue 'C42'"),
    ("the mayor", ["C3"], "unknown character 'the mayor'"),
])
def test_bad_accusations_are_errors_and_the_game_goes_on(ctx, who, clues, error):
    grant(ctx, "C3")
    before = events.state_hash(ctx.game.conn, ctx.game.run_id)
    out = accuse(ctx, who, clues)
    assert out["ok"] is False and out["error"] == error
    assert events.state_hash(ctx.game.conn, ctx.game.run_id) == before
    assert state.read(ctx.game)["status"] == "active"


def test_after_the_ending_every_tool_says_game_over(ctx):
    grant(ctx, "C3", "C4")
    accuse(ctx, "tom", ["C3", "C4"])
    for name, args in (("look", {}), ("notebook", {}), ("hint", {}),
                       ("accuse", {"character": "tom", "motive": "x", "clue_ids": []})):
        assert REGISTRY.call(ctx, name, args, "gm") == {"ok": False, "error": "game over"}


def test_a_timeout_ends_with_the_true_timeline(ctx):
    for _ in range(144):
        REGISTRY.call(ctx, "look", {}, "gm")
    end = REGISTRY.call(ctx, "end_turn", {}, "engine")
    assert end["result"] == "timeout" and end["missed"] == ["C2", "C3", "C4", "C5", "C6"]
    assert end["true_timeline"][-1] == "03:00 The tavern closes."


def test_the_accusation_is_scored_by_the_engine_not_the_model(make_session):
    session = make_session()
    session.turn("talk mira: anyone on the quay last night?")
    result = session.turn("accuse tom: monopoly C3 C5")          # C5 never found
    assert "clue C5 is not in the notebook" in result.narration
    result = session.turn("accuse tom: monopoly C3")
    assert "Result: PARTIAL" in result.narration and result.status == "ended"
