"""Hints: the stuck nudge after 8 turns, pointer hints, and the `hint` tool."""

import pytest

from harbor.engine import events, state
from harbor.engine.case_loader import load_case
from harbor.tools import REGISTRY

from conftest import CASE_PATH

CASE = load_case(CASE_PATH)
KNOWS = {(k.character_id, k.clue_id) for k in CASE.knowledge}
NAMES = {c.name: c.id for c in CASE.characters}


def armed(session):
    g = session.ctx.game
    return [(e["turn"], e["payload"]["clue_id"]) for e in events.log(g.conn, g.run_id)
            if e["type"] == "nudge_armed"]


def stall(session, turns):
    for _ in range(turns):
        session.turn("notebook")


def test_a_nudge_arms_after_eight_turns_without_a_new_clue(make_session):
    session = make_session()
    session.turn("look")                         # turn 1 finds C1
    stall(session, 7)                            # turns 2-8: counter 7
    assert armed(session) == []
    stall(session, 1)                            # turn 9: counter 8
    assert armed(session) == [(9, "C2")]         # first unfound clue in the nudge order
    assert state.read(session.ctx.game)["nudge_clue"] == "C2"
    stall(session, 3)
    assert armed(session) == [(9, "C2")]         # armed once, not again every turn


def test_the_next_conversation_with_someone_who_knows_delivers_it(make_session):
    session = make_session()
    session.turn("look")
    stall(session, 8)
    session.turn("go office")
    result = session.turn("talk odrin: Cold morning, isn't it?")   # off topic for C2
    found = result.tool_results[0][2]["new_clues"]
    assert "C2" in found
    st = state.read(session.ctx.game)
    assert st["nudge_clue"] is None and st["turns_since_new_clue"] == 0


def test_nudges_follow_the_order_and_skip_found_clues(make_session):
    session = make_session()
    session.turn("talk mira: anyone about on the quay last night?")   # C3 found early
    stall(session, 8)
    session.turn("go office")
    session.turn("talk odrin: morning")                                 # nudged C2 delivered
    stall(session, 8)
    assert [clue for _, clue in armed(session)] == ["C2", "C4"]         # C3 skipped


def test_every_nudge_is_a_real_unfound_clue_in_order(make_session):
    session = make_session()
    for _ in range(40):
        session.turn("notebook")
        nudge = state.read(session.ctx.game)["nudge_clue"]
        if nudge:
            assert nudge in CASE.nudge_order
            assert nudge not in state.found_clue_ids(session.ctx.game)


def test_a_guarded_clue_is_nudged_as_a_pointer_not_handed_over(make_session):
    session = make_session()
    for line in ("go office", "talk odrin: tell me about the town meeting",          # C2
                 "go quay", "talk mira: what did you see last night on the quay?",   # C3
                 "go chandlery", "go lighthouse",
                 "talk bram: any boats on the water last night?",                    # C4
                 "go chandlery"):
        session.turn(line)
    assert {"C2", "C3", "C4"} <= state.found_clue_ids(session.ctx.game)
    stall(session, 8)
    assert state.read(session.ctx.game)["nudge_clue"] == "C5"
    result = session.turn("talk ysolde: nice shop")
    assert "C5" not in result.tool_results[0][2]["new_clues"]          # trust 0: still guarded
    ctx = session.ctx
    recall = REGISTRY.call(ctx, "recall_knowledge", {}, "npc")
    assert recall["guarded"] is True and recall["clues"] == []
    session.turn("go lighthouse")
    session.turn("talk bram: hello again")
    recall = REGISTRY.call(ctx, "recall_knowledge", {}, "npc")
    assert {"clue": "C5", "ask": "Ysolde Crane", "nudge": True} == {
        k: v for k, v in recall["pointers"][0].items() if k in ("clue", "ask", "nudge")}


def test_someone_with_no_pointer_row_still_sends_the_player_on(make_session):
    session = make_session()
    session.turn("look")
    stall(session, 8)                                   # C2 armed
    session.turn("go fish market")
    session.turn("talk tom: lovely fish")               # Tom knows nothing about C2
    recall = REGISTRY.call(session.ctx, "recall_knowledge", {}, "npc")
    nudge = [p for p in recall["pointers"] if p.get("nudge")]
    assert nudge and (NAMES[nudge[0]["ask"]], "C2") in KNOWS


@pytest.mark.parametrize("character", [c.id for c in CASE.characters])
def test_pointer_hints_name_a_character_who_really_knows_the_clue(make_session, character):
    session = make_session()
    ctx = session.ctx
    session.turn("look")
    stall(session, 8)                                   # also arms a nudge pointer
    from test_isolation import make_active
    from harbor.tools import Draft
    make_active(ctx, character, runner=lambda *a: Draft("Hm."))
    recall = REGISTRY.call(ctx, "recall_knowledge", {}, "npc")
    for pointer in recall["pointers"]:
        assert (NAMES[pointer["ask"]], pointer["clue"]) in KNOWS, pointer


def test_case_pointers_all_name_knowers():
    for p in CASE.pointers:
        assert (p.points_to_character, p.clue_id) in KNOWS, p


def test_hint_names_a_person_and_place_never_the_clue(ctx):
    out = REGISTRY.call(ctx, "hint", {}, "gm")
    assert out == {"ok": True, "target": {"character": "Odrin Vale",
                                          "place": "Harbormaster's Office"}, "clock": "06:20"}
    log = events.log(ctx.game.conn, ctx.game.run_id)
    assert log[-1]["type"] == "hint_used" and log[-1]["payload"]["clue_id"] == "C2"


def test_hint_prefers_people_not_yet_spoken_to(ctx):
    from harbor.tools import Draft
    REGISTRY.call(ctx, "go", {"place": "office"}, "gm")
    ctx.npc_runner = lambda *a: Draft("Busy.")
    REGISTRY.call(ctx, "talk", {"character": "odrin", "message": "weather?"}, "gm")
    assert REGISTRY.call(ctx, "hint", {}, "gm")["target"]["character"] == "Hale Pruitt"


def test_hint_when_nothing_is_left(make_session):
    session = make_session()
    ctx = session.ctx
    for line in ("go office", "talk odrin: the town meeting?", "go quay",
                 "talk mira: last night on the quay?", "go chandlery", "go lighthouse",
                 "talk bram: boats at night?", "go chandlery",
                 "talk ysolde: please, who bought cutters?",
                 "talk ysolde: please, it matters. who bought cutters?",
                 "go quay", "go fish market", "go salt cask",
                 "talk hale: was Tom here at midnight?"):
        session.turn(line)
    assert set(CASE.nudge_order) <= state.found_clue_ids(ctx.game)
    out = REGISTRY.call(ctx, "hint", {}, "gm")
    assert out["ok"] and out["target"] is None
