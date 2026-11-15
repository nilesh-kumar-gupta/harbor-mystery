"""Knowledge isolation: no character can put a clue they do not know into the
notebook, whatever the model asks for. (The referee half arrives in M4.)"""

import pytest

from harbor.engine import events
from harbor.engine.case_loader import load_case
from harbor.tools import REGISTRY, Draft

from conftest import CASE_PATH

CASE = load_case(CASE_PATH)
KNOWS = {(k.character_id, k.clue_id) for k in CASE.knowledge}
UNKNOWN = [(c.id, clue.id) for c in CASE.characters for clue in CASE.clues
           if (c.id, clue.id) not in KNOWS]
ROUTE = {"quay": [], "office": ["office"], "fish_market": ["fish market"],
         "chandlery": ["chandlery"], "salt_cask": ["fish market", "salt cask"],
         "lighthouse": ["chandlery", "lighthouse"]}


def make_active(ctx, character_id, runner=None):
    """Walk to a character and talk to them, through tools only, so they are active."""
    start = next(c.start_place for c in CASE.characters if c.id == character_id)
    for place in ROUTE[start]:
        assert REGISTRY.call(ctx, "go", {"place": place}, "gm")["ok"]
    ctx.npc_runner = runner or (lambda *a: Draft("Hm."))
    return REGISTRY.call(ctx, "talk", {"character": character_id, "message": "hello"}, "gm")


@pytest.mark.parametrize("character_id, clue_id", UNKNOWN)
def test_reveal_clue_refuses_a_clue_the_character_does_not_know(ctx, character_id, clue_id):
    assert make_active(ctx, character_id)["ok"]
    before = events.state_hash(ctx.game.conn, ctx.game.run_id)
    out = REGISTRY.call(ctx, "reveal_clue", {"clue_id": clue_id}, "npc")
    assert out == {"ok": False, "error": f"you do not know clue {clue_id}"}
    assert events.state_hash(ctx.game.conn, ctx.game.run_id) == before


@pytest.mark.parametrize("character_id", [c.id for c in CASE.characters])
def test_recall_knowledge_returns_only_the_active_characters_rows(ctx, character_id):
    assert make_active(ctx, character_id)["ok"]
    out = REGISTRY.call(ctx, "recall_knowledge", {}, "npc")
    assert {c["id"] for c in out["clues"]} <= {cl for ch, cl in KNOWS if ch == character_id}
    own_pointers = {p.clue_id for p in CASE.pointers if p.character_id == character_id}
    assert {p["clue"] for p in out["pointers"]} <= own_pointers


@pytest.mark.parametrize("clue_id", ["C99", "", "c3", "C3; DROP TABLE clues"])
def test_unknown_clue_ids_are_refused(ctx, clue_id):
    assert make_active(ctx, "mira")["ok"]
    assert REGISTRY.call(ctx, "reveal_clue", {"clue_id": clue_id}, "npc") == {
        "ok": False, "error": f"unknown clue {clue_id!r}"}
