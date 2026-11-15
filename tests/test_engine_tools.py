"""Engine rules, per tool: good calls change state through events; bad calls
return an error value and change nothing."""

import pytest

from harbor.cli import play_loop
from harbor.engine import events, state
from harbor.tools import REGISTRY


def snapshot(ctx):
    g = ctx.game
    return events.state_hash(g.conn, g.run_id), len(events.log(g.conn, g.run_id))


def test_start_state(ctx):
    st = state.read(ctx.game)
    assert (st["place"], st["clock"], st["turn"], st["status"]) == ("quay", 360, 1, "active")
    assert REGISTRY.call(ctx, "start_run", {}, "engine")["ok"] is False


def test_look_returns_the_place_and_files_visible_clues(ctx, call):
    out = call("look")
    assert out["ok"] and out["place"] == "Harbor Quay" and out["clock"] == "06:05"
    assert out["present"] == ["Mira Tolle"]
    assert out["exits"] == ["Harbormaster's Office", "Fish Market", "Chandlery"]
    assert [c["id"] for c in out["clues"]] == ["C1"]
    assert state.found_clue_ids(ctx.game) == {"C1"}
    types = [e["type"] for e in events.log(ctx.game.conn, ctx.game.run_id)]
    assert types == ["run_started", "clock_advanced", "clue_found"]


def test_look_twice_does_not_duplicate_the_clue(ctx, call):
    call("look")
    out = call("look")
    assert out["ok"] and out["clock"] == "06:10" and [c["id"] for c in out["clues"]] == ["C1"]
    assert len(state.notebook(ctx.game)) == 1


def test_go_moves_and_costs_ten_minutes(ctx, call):
    out = call("go", place="chandlery")
    assert out == {"ok": True, "place": "Chandlery", "present": ["Ysolde Crane"],
                   "clock": "06:10"}
    assert state.read(ctx.game)["place"] == "chandlery"
    assert call("go", place="the Lighthouse")["ok"]
    assert call("go", place="Harbor Quay")["ok"] is False  # not adjacent to the lighthouse


@pytest.mark.parametrize("place, message", [
    ("lighthouse", "no exit from Harbor Quay to Lighthouse"),
    ("moon", "unknown place 'moon'"),
    ("quay", "already at Harbor Quay"),
    ("", "unknown place ''"),
])
def test_bad_go_is_an_error_value_and_changes_nothing(ctx, call, place, message):
    before = snapshot(ctx)
    out = call("go", place=place)
    assert out["ok"] is False and out["error"] == message
    assert out["exits"] == ["Harbormaster's Office", "Fish Market", "Chandlery"]
    assert snapshot(ctx) == before


def test_notebook_lists_clues_and_is_free(ctx, call):
    assert call("notebook") == {"ok": True, "clues": []}
    call("look")
    before = snapshot(ctx)
    out = call("notebook")
    assert out["clues"] == [{"id": "C1", "source": "seen", "place": "Harbor Quay", "at": "06:05",
                             "text": "The winch cable was cut clean, and the pins were knocked loose."}]
    assert snapshot(ctx) == before


@pytest.mark.parametrize("name, arguments, audience, message", [
    ("fly", {}, "gm", "unknown tool 'fly'"),
    ("end_turn", {}, "gm", "unknown tool 'end_turn'"),      # engine steps are not model tools
    ("look", {}, "npc", "unknown tool 'look'"),
    ("go", {}, "gm", "missing argument 'place'"),
    ("go", {"place": 3}, "gm", "argument 'place' must be of type string"),
    ("look", {"character": "tom"}, "gm", "unexpected argument 'character'"),
    ("go", "quay", "gm", "arguments must be an object"),
])
def test_registry_refuses_bad_calls_as_values(ctx, name, arguments, audience, message):
    before = snapshot(ctx)
    out = REGISTRY.call(ctx, name, arguments, audience)
    assert out["ok"] is False and out["error"] == message
    assert snapshot(ctx) == before


def test_a_crashing_tool_becomes_an_error_value_and_is_rolled_back(ctx, monkeypatch):
    before = snapshot(ctx)
    monkeypatch.setattr(state, "advance_clock", lambda *a: 1 / 0)
    out = REGISTRY.call(ctx, "go", {"place": "office"}, "gm")   # fails after player_moved
    assert out["ok"] is False and "ZeroDivisionError" in out["error"]
    assert ctx.game.tool_depth == 0
    assert snapshot(ctx) == before and state.read(ctx.game)["place"] == "quay"


def test_registry_never_raises_on_a_malformed_call(ctx):
    before = snapshot(ctx)
    assert REGISTRY.call(ctx, ["look"], {}, "gm")["ok"] is False
    assert REGISTRY.call(ctx, None, None, "gm")["ok"] is False
    assert snapshot(ctx) == before


def test_tools_before_start_run_say_so(case):
    from harbor.tools import Context
    fresh = Context(game=state.new_game(case, "unstarted"))
    for name, audience in (("look", "gm"), ("end_turn", "engine")):
        assert REGISTRY.call(fresh, name, {}, audience) == {"ok": False, "error": "run not started"}


def test_state_cannot_change_outside_a_tool_call(ctx):
    with pytest.raises(events.WriteOutsideTool):
        state.advance_clock(ctx.game, 10)
    with pytest.raises(events.WriteOutsideTool):
        state.move(ctx.game, "office")


def test_end_turn_counts_turns_without_a_new_clue(ctx, call):
    end = lambda: REGISTRY.call(ctx, "end_turn", {}, "engine")
    call("look")
    assert end()["turns_since_new_clue"] == 0      # C1 was new this turn
    call("look")
    assert end()["turns_since_new_clue"] == 1
    out = end()
    assert out["turns_since_new_clue"] == 2 and out["turn"] == 4


def test_the_day_ends_at_1800_and_then_every_tool_refuses(ctx, call):
    for _ in range(72):                             # 72 moves of 10 minutes = 12 hours
        assert call("go", place="office" if state.read(ctx.game)["place"] == "quay" else "quay")["ok"]
    assert REGISTRY.call(ctx, "end_turn", {}, "engine")["result"] == "timeout"
    st = state.read(ctx.game)
    assert (st["status"], st["result"], st["clock"]) == ("ended", "timeout", 18 * 60)
    before = snapshot(ctx)
    for name, arguments in (("look", {}), ("go", {"place": "quay"}), ("notebook", {})):
        assert call(name, **arguments) == {"ok": False, "error": "game over"}
    assert REGISTRY.call(ctx, "end_turn", {}, "engine") == {
        "ok": True, "status": "ended", "result": "timeout"}
    assert snapshot(ctx) == before


def test_no_action_starts_after_1800_even_within_one_turn(ctx, call):
    for _ in range(71):
        call("go", place="office" if state.read(ctx.game)["place"] == "quay" else "quay")
    assert call("look")["clock"] == "17:55"
    assert call("look")["clock"] == "18:00"                 # started before the end: allowed
    before = snapshot(ctx)
    for name, arguments in (("go", {"place": "quay"}), ("look", {}), ("notebook", {})):
        assert call(name, **arguments) == {"ok": False, "error": "the day is over"}
    assert snapshot(ctx) == before
    assert REGISTRY.call(ctx, "end_turn", {}, "engine")["result"] == "timeout"


def test_replaying_the_event_log_rebuilds_the_same_state(ctx, call, case):
    call("look")
    call("go", place="fish market")
    REGISTRY.call(ctx, "end_turn", {}, "engine")
    call("go", place="salt cask")
    g = ctx.game
    fresh = state.new_game(case, "another-run")
    events.replay(fresh.conn, fresh.run_id, events.log(g.conn, g.run_id))
    assert events.state_hash(fresh.conn, fresh.run_id) == events.state_hash(g.conn, g.run_id)


def test_case_tables_hold_the_loaded_case(ctx, case):
    count = lambda t: ctx.game.conn.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0]
    assert count("places") == len(case.places) and count("characters") == len(case.characters)
    assert count("clues") == len(case.clues) and count("knowledge") == len(case.knowledge)
    assert count("exits") == 2 * len(case.exits) and count("case_solution") == 1
    assert count("labels") == 0
    run = ctx.game.conn.execute("SELECT * FROM runs").fetchone()
    assert (run["run_id"], run["seed"], run["case_id"]) == ("test-run", 1, "saltmere")


def test_play_refuses_an_existing_database(case, tmp_path, capsys):
    db = tmp_path / "run.sqlite"
    quiet = dict(out=lambda *_: None, traces_dir=tmp_path)
    assert play_loop(case, 0, str(db), lines=["look"], **quiet) == 0
    assert play_loop(case, 0, str(db), lines=["look"], **quiet) == 1
    assert len(list(tmp_path.glob("*.jsonl"))) == 1
    assert "already exists" in capsys.readouterr().err


def test_plain_play_loop(case):
    printed = []
    lines = ["look", "dance", "dance", "go lighthouse", "go to the chandlery", "notebook", "quit", "look"]
    assert play_loop(case, 0, ":memory:", lines=lines, out=printed.append) == 0
    text = "\n".join(printed)
    assert "You notice: The winch cable was cut clean" in text
    assert "(not a command)" in text
    assert "(no exit from Harbor Quay to Lighthouse) Exits:" in text
    assert "You walk to Chandlery. [06:15] Here: Ysolde Crane." in text
    assert "C1  The winch cable" in text
    assert "You walk to Chandlery. [06:15]" in text   # the typos cost no time


def test_plain_play_loop_is_traced_per_turn(case, tmp_path):
    db = tmp_path / "run.sqlite"
    play_loop(case, 0, str(db), lines=["look", "dance", "go office"], out=lambda *_: None,
              traces_dir=tmp_path)
    from harbor.engine.schema import connect
    rows = connect(str(db)).execute("SELECT trace_id, name FROM spans ORDER BY rowid").fetchall()
    turns = [(r["trace_id"].rsplit("-", 1)[1], r["name"]) for r in rows]
    assert turns == [("setup", "start_run"),
                     ("t0001", "player_turn"), ("t0001", "game_master"), ("t0001", "offline_turn"), ("t0001", "chat"), ("t0001", "look"),
                     ("t0001", "chat"), ("t0001", "close_turn"),
                     ("t0002", "player_turn"), ("t0002", "game_master"), ("t0002", "offline_turn"), ("t0002", "chat"), ("t0002", "go"),
                     ("t0002", "chat"), ("t0002", "close_turn")]


def test_commands_piped_from_a_file_are_echoed(monkeypatch, capsys):
    import io

    from harbor import cli
    monkeypatch.setattr("sys.stdin", io.StringIO("look\ngo office\n"))
    assert list(cli._stdin_lines()) == ["look", "go office"]
    assert capsys.readouterr().out == "> look\n> go office\n> \n"


def test_the_cli_has_no_hosted_mode():
    import pytest

    from harbor import cli
    with pytest.raises(SystemExit) as exit_info:
        cli.main(["play", "--hosted"])
    assert exit_info.value.code == 2          # argparse: unrecognized argument
