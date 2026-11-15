import pytest

from harbor.referee import parse_verdict, referee_context


@pytest.mark.parametrize("value,expected", [
    ("OK", "OK"), (" LEAK\n", "LEAK"), ("\tOK\n", "OK"),
    ("ok", "INVALID"), ("OK.", "INVALID"), ("OK but", "INVALID"),
    ("LEAK?", "INVALID"), ("", "INVALID"), (None, "INVALID"),
    ({"verdict": "OK"}, "INVALID"), ('{"verdict":"OK"}', "INVALID"),
    ("OK" + "x" * 10000, "INVALID"), (1, "INVALID"),
])
def test_whole_verdict_is_strict(value, expected):
    assert parse_verdict(value) == expected


def test_referee_context_has_only_allowed_facts(ctx):
    card = dict(ctx.game.conn.execute("SELECT * FROM characters WHERE id='mira'").fetchone())
    content = referee_context(ctx.game, card, "hello", "hello")
    assert set(content) == {"character", "allowed_facts", "may_send_the_player_to", "player_said", "facts_the_player_stated", "reply"}
    assert content["allowed_facts"] == []
    assert content["reply"] == "hello"



def test_referee_context_positive_fact_excludes_other_knowledge(ctx):
    import json
    from harbor.tools import REGISTRY
    ids = REGISTRY.call(ctx, "begin_talk", {"character": "mira", "message": "night"}, "gm")
    args = {"interaction_id": ids["interaction_id"], "attempt": 1}
    assert REGISTRY.call(ctx, "npc_context", args, "npc")["ok"]
    assert REGISTRY.call(ctx, "reveal_clue", {**args, "clue_id": "C3"}, "npc")["ok"]
    assert REGISTRY.call(ctx, "submit_draft", {**args, "text": "Morning."}, "npc")["ok"]
    content = REGISTRY.call(ctx, "referee_context", args, "referee")
    clue = lambda cid: ctx.game.conn.execute("SELECT text FROM clues WHERE id=?", (cid,)).fetchone()[0]
    assert content["allowed_facts"] == [clue("C3")]
    assert clue("C5") not in json.dumps(content)
    assert "implicates" not in json.dumps(content)
