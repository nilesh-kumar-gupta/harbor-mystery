import json
import os
import sqlite3
import subprocess
import sys

import pytest

from harbor.tools import REGISTRY


@pytest.fixture
def server(tmp_path):
    proc = subprocess.Popen([sys.executable, "-m", "harbor.mcp_server"],
        stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        text=True, env={**os.environ, "HARBOR_HOME": str(tmp_path)})
    def send(method, params=None):
        proc.stdin.write(json.dumps({"jsonrpc": "2.0", "id": 1, "method": method,
                                    "params": params or {}}) + "\n")
        proc.stdin.flush()
        return json.loads(proc.stdout.readline())
    yield proc, send, tmp_path
    proc.stdin.close()
    proc.wait(timeout=10)
    assert proc.returncode == 0, proc.stderr.read()


def tool(send, name, **args):
    result = send("tools/call", {"name": name, "arguments": args, "_meta": {"test": "caller"}})["result"]
    assert result["isError"] is False
    assert len(result["content"]) == 1
    return json.loads(result["content"][0]["text"])


def test_full_scripted_conversation(server):
    proc, send, home = server
    assert send("initialize", {"protocolVersion": "2025-06-18"})["result"]["capabilities"] == {"tools": {}}
    tools = send("tools/list")["result"]["tools"]
    expected = {t["name"]: t for a in ("gm", "npc", "referee") for t in REGISTRY.schemas(a)}
    assert {t["name"] for t in tools} == set(expected)
    for entry in tools:
        assert entry["_meta"] == {"anthropic/alwaysLoad": True}
        assert entry["inputSchema"] == expected[entry["name"]]["input_schema"]
    assert tool(send, "game_status")["loaded"] is False
    assert not tool(send, "look")["ok"]
    run = tool(send, "new_game", seed=3)
    assert run["ok"]
    assert tool(send, "player_turn", line="Hello Mira")["ok"]
    begin = tool(send, "begin_talk", character="Mira", message="hello")
    ids = {"interaction_id": begin["interaction_id"], "attempt": 1}
    assert tool(send, "npc_context", **ids)["ok"]
    assert tool(send, "recall_knowledge", **ids)["ok"]
    assert tool(send, "submit_draft", **ids, text="Hm.")["ok"]
    assert tool(send, "referee_context", **ids)["ok"]
    assert tool(send, "submit_verdict", **ids, verdict="OK")["ok"]
    final = tool(send, "finalize_talk", interaction_id=ids["interaction_id"])
    assert final["reply"] == "Hm." and final["status"] == "committed"
    assert tool(send, "close_turn")["ok"]
    assert not tool(send, "start_run")["ok"]
    assert not tool(send, "unknown")["ok"]
    with sqlite3.connect(home / "runs" / f"{run['run_id']}.sqlite") as conn:
        attrs = [json.loads(r[0]) for r in conn.execute("SELECT attrs FROM spans WHERE kind='tool_call'")]
    assert attrs and all(a["mcp.meta"] == {"test": "caller"} for a in attrs)


def test_protocol_errors_continue(server):
    proc, send, _ = server
    proc.stdin.write("broken\n")
    proc.stdin.flush()
    assert json.loads(proc.stdout.readline())["error"]["code"] == -32700
    assert send("unknown")["error"]["code"] == -32601
    assert send("tools/call", {"name": 1})["error"]["code"] == -32602
    proc.stdin.write(json.dumps({"jsonrpc": "2.0", "method": "notifications/initialized"}) + "\n")
    proc.stdin.flush()
    assert send("ping")["result"] == {}


def test_mcp_requires_player_turn(server):
    _, send, _ = server
    assert tool(send, "new_game")["ok"]
    for _ in range(6):
        assert tool(send, "look")["error"] == "start a player turn first"
    assert tool(send, "game_status")["ok"]
    assert tool(send, "player_turn", line="look")["ok"]
    for _ in range(3):
        assert tool(send, "look")["ok"]
    assert tool(send, "look")["error"] == "game master tool limit"
