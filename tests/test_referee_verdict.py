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
