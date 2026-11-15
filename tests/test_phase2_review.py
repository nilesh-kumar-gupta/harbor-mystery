"""Regression tests for the supervisor's final Phase 2 findings."""
import json
import os
from pathlib import Path
import sqlite3
import subprocess
import sys

import pytest

from harbor.tools import Context, REGISTRY
from harbor.tracing import Tracer


class Client:
    def __init__(self, home):
        self.proc = subprocess.Popen([sys.executable, "-m", "harbor.mcp_server"],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            text=True, env={**os.environ, "HARBOR_HOME": str(home)})

    def call(self, name, **args):
        request = {"jsonrpc": "2.0", "id": 1, "method": "tools/call",
                   "params": {"name": name, "arguments": args, "_meta": {"review": True}}}
        self.proc.stdin.write(json.dumps(request) + "\n")
        self.proc.stdin.flush()
        response = json.loads(self.proc.stdout.readline())
        return json.loads(response["result"]["content"][0]["text"])

    def stop(self, crash=False):
        if self.proc.poll() is None:
            if crash:
                self.proc.kill()
            else:
                self.proc.stdin.close()
            self.proc.wait(timeout=10)
        stderr = self.proc.stderr.read()
        assert not stderr, stderr


@pytest.mark.parametrize("phase", ["drafting", "evaluating"])
def test_live_lease_crash_recovery_and_next_turn(tmp_path, phase):
    first, second = Client(tmp_path), Client(tmp_path)
    try:
        run = first.call("new_game")["run_id"]
        assert first.call("player_turn", line="hello")["ok"]
        iid = first.call("begin_talk", character="Mira", message="hello")["interaction_id"]
        ids = {"interaction_id": iid, "attempt": 1}
        assert first.call("npc_context", **ids)["ok"]
        if phase == "evaluating":
            assert first.call("submit_draft", **ids, text="Hm.")["ok"]
        blocked = second.call("resume_game", run_id=run)
        assert not blocked["ok"]
        assert f"run {run} is open in another session (pid {first.proc.pid})" in blocked["error"]
        first.stop(crash=True)
        recovered = second.call("resume_game", run_id=run)
        assert recovered["ok"] and len(recovered["recovered"]) == 1
        assert second.call("player_turn", line="continue")["turn"] == 2
        assert second.call("look")["ok"]
        assert second.call("close_turn")["ok"]
        assert second.call("game_status")["trace_failures"] == 0
        assert second.call("resume_game", run_id=run)["recovered"] == []
        with sqlite3.connect(tmp_path / "runs" / f"{run}.sqlite") as conn:
            spans = conn.execute("SELECT attrs FROM spans WHERE name='resume_game'").fetchall()
            assert spans and all(json.loads(r[0])["mcp.meta"] == {"review": True} for r in spans)
            assert conn.execute("SELECT COUNT(*) FROM conversations WHERE speaker='npc'").fetchone()[0] == 1
    finally:
        first.stop()
        second.stop()
    # A clean shutdown releases the lease too.
    third = Client(tmp_path)
    try:
        assert third.call("resume_game", run_id=run)["ok"]
    finally:
        third.stop()


def test_deep_json_does_not_kill_server(tmp_path):
    client = Client(tmp_path)
    try:
        client.proc.stdin.write("[" * 200000 + "\n")
        client.proc.stdin.flush()
        assert json.loads(client.proc.stdout.readline())["error"]["code"] == -32700
        assert client.call("game_status") == {"ok": True, "loaded": False}
    finally:
        client.stop()


def test_switching_runs_keeps_counts_and_closes_old_spans(tmp_path, monkeypatch):
    monkeypatch.setenv("HARBOR_HOME", str(tmp_path))
    from harbor.tools.session_tools import release_lease
    def call(ctx, name, **args):
        result = REGISTRY.call(ctx, name, args, "gm")
        assert result["ok"], result
        return result
    # Two saved runs, each at the first turn.
    saved = []
    for _ in range(2):
        ctx = Context()
        saved.append(call(ctx, "new_game")["run_id"])
        release_lease(ctx)
        ctx.game.conn.close()
    ctx = Context(require_turn=True)
    try:
        call(ctx, "resume_game", run_id=saved[0])
        call(ctx, "player_turn", line="look")
        for _ in range(3):
            call(ctx, "look")
        old = ctx.game
        call(ctx, "resume_game", run_id=saved[1])
        assert not old.tracer._virtual
        with pytest.raises(sqlite3.ProgrammingError):
            old.conn.execute("SELECT 1")
        call(ctx, "resume_game", run_id=saved[0])
        assert ctx.counts[(saved[0], "gm", 1)] == 3
        assert call(ctx, "player_turn", line="continue")["turn"] == 2
        for _ in range(3):
            call(ctx, "look")
        assert REGISTRY.call(ctx, "look", {}, "gm")["error"] == "game master tool limit"
        assert ctx.game.conn.execute("SELECT status FROM spans WHERE span_id=?",
            (f"{saved[0]}-t0001-gm",)).fetchone()[0] == "interrupted"
    finally:
        release_lease(ctx)
        ctx.game.conn.close()


def test_reopened_tracer_uses_highest_numeric_suffix(ctx):
    tracer = ctx.tracer
    tracer.open_virtual("agent", "unwritten", "virtual")
    with tracer.span("tool_call", "one"):
        pass
    with tracer.span("tool_call", "two"):
        pass
    reopened = Tracer(ctx.game.conn, ctx.game.run_id)
    with reopened.span("tool_call", "after_crash"):
        pass
    assert reopened.trace_failures == 0


def test_cli_import_does_not_load_server():
    result = subprocess.run([sys.executable, "-c",
        "import harbor.cli, sys; assert 'harbor.mcp_server' not in sys.modules"],
        capture_output=True, text=True)
    assert result.returncode == 0, result.stderr


def test_player_turn_failure_does_not_mark_turn_open(ctx):
    ctx.begin_turn()
    span = ctx.tracer.open_virtual("agent", "game_master", f"{ctx.tracer.trace_id}-gm")
    ctx.tracer.close_virtual(span.span_id)
    result = REGISTRY.call(ctx, "player_turn", {"line": "hello"}, "gm")
    assert not result["ok"]
    assert not ctx.turn_open


def test_session_close_releases_ownership(tmp_path, monkeypatch):
    monkeypatch.setenv("HARBOR_HOME", str(tmp_path))
    from harbor.session import Session
    ctx = Context()
    assert REGISTRY.call(ctx, "new_game", {}, "gm")["ok"]
    run = ctx.game.run_id
    Session(ctx, None).close()
    reopened = Session.open(run)
    reopened.close()


def test_npc_caps_are_scoped_to_the_run(tmp_path, monkeypatch):
    monkeypatch.setenv("HARBOR_HOME", str(tmp_path))
    from harbor.tools.session_tools import release_lease
    saved = []
    for _ in range(2):
        ctx = Context()
        saved.append(REGISTRY.call(ctx, "new_game", {"seed": 1}, "gm")["run_id"])
        release_lease(ctx)
        ctx.game.conn.close()
    ctx = Context()
    iids = []
    try:
        for run in saved:
            assert REGISTRY.call(ctx, "resume_game", {"run_id": run}, "gm")["ok"]
            assert REGISTRY.call(ctx, "player_turn", {"line": "hello"}, "gm")["ok"]
            iid = REGISTRY.call(ctx, "begin_talk", {"character": "Mira", "message": "hello"}, "gm")["interaction_id"]
            iids.append(iid)
            ids = {"interaction_id": iid, "attempt": 1}
            assert REGISTRY.call(ctx, "npc_context", ids, "npc")["calls_left"] == 4
            for _ in range(4):
                assert REGISTRY.call(ctx, "recall_knowledge", ids, "npc")["ok"]
            assert REGISTRY.call(ctx, "recall_knowledge", ids, "npc")["error"] == "NPC tool limit"
        assert iids[0] == iids[1]  # case ids are local to each database
    finally:
        release_lease(ctx)
        ctx.game.conn.close()


def test_game_end_releases_lease(tmp_path):
    first, second = Client(tmp_path), Client(tmp_path)
    try:
        run = first.call("new_game")["run_id"]
        assert first.call("player_turn", line="accuse")["ok"]
        assert first.call("accuse", character="Tom", motive="test", clue_ids=[])["ok"]
        assert second.call("resume_game", run_id=run)["ok"]
    finally:
        first.stop()
        second.stop()
