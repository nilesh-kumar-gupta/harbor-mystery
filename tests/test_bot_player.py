"""The bot player solves the case through the full architecture, with no human input."""

from harbor.bot import Bot
from harbor.engine import state


def test_the_bot_wins_in_under_40_turns(make_session):
    session = make_session()
    game = Bot(session).play()
    assert game.result == "win", game.transcript[-1]
    assert game.turns < 40
    assert game.accusation == "Tom Brask"
    ended = session.ctx.game.conn.execute(
        "SELECT payload FROM events WHERE type = 'game_ended'").fetchone()["payload"]
    import json
    cited = json.loads(ended)["clue_ids"]
    required = {"C2", "C3", "C4", "C5", "C6"}
    assert len(set(cited) & required) >= 2
    assert set(cited) <= state.found_clue_ids(session.ctx.game)


def test_harbor_bot_command(capsys):
    from harbor.cli import main
    assert main(["bot", "--seed", "3"]) == 0
    assert "Bot finished in" in capsys.readouterr().out


def test_the_bot_decides_only_from_narration(make_session):
    """Its inputs are the lines it typed and the narration it got back."""
    session = make_session()
    bot = Bot(session)
    game = bot.play()
    assert all(isinstance(line, str) and isinstance(text, str) for line, text in game.transcript)
    from harbor.session import Session
    from harbor.tools import Context
    held = list(vars(bot).values()) + [c.cell_contents for c in bot._turn.__closure__]
    assert not any(isinstance(v, Context) for v in held)
    assert [type(v) for v in held if isinstance(v, Session)] == [Session]  # only inside the view
    assert set(vars(bot)) == {"_turn", "max_turns", "game", "place", "exits", "people_at",
                              "asked", "leads", "clues", "visited"}


def test_the_bot_keeps_inside_its_turn_budget(make_session):
    for budget in (8, 12, 20):
        assert Bot(make_session(run_id=f"b{budget}"), max_turns=budget).play().turns <= budget
