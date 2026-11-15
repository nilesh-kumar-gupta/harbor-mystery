import importlib.util
import json
from pathlib import Path
import subprocess
import sys

import pytest

from harbor.tools import Context, REGISTRY

SCRIPT = Path(__file__).parents[1] / "traces/report.py"
spec = importlib.util.spec_from_file_location("trace_report", SCRIPT)
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


def call(ctx, name, **args):
    result = REGISTRY.call(ctx, name, args, REGISTRY.get(name).audience, meta={"source": "test"})
    assert result["ok"], result
    return result


@pytest.fixture
def run(tmp_path, monkeypatch):
    monkeypatch.setenv("HARBOR_HOME", str(tmp_path))
    ctx = Context()
    call(ctx, "new_game")
    call(ctx, "player_turn", line="Hello")
    # One rejected semantic leak followed by an accepted retry.
    first = call(ctx, "begin_talk", character="Mira", message="hello")["interaction_id"]
    for attempt, verdict in ((1, "LEAK"), (2, "OK")):
        ids = {"interaction_id": first, "attempt": attempt}
        call(ctx, "npc_context", **ids)
        call(ctx, "recall_knowledge", **ids)
        call(ctx, "submit_draft", **ids, text="Hm.")
        call(ctx, "submit_verdict", **ids, verdict=verdict)
        call(ctx, "finalize_talk", interaction_id=first)
    second = call(ctx, "begin_talk", character="Mira", message="hello")["interaction_id"]
    for attempt in (1, 2, 3):
        ids = {"interaction_id": second, "attempt": attempt}
        call(ctx, "npc_context", **ids)
        call(ctx, "submit_draft", **ids, text="Hm.")
        if attempt == 1:
            call(ctx, "submit_verdict", **ids, verdict="OK but")
        call(ctx, "finalize_talk", interaction_id=second)
    call(ctx, "close_turn")
    path = Path(call(ctx, "game_status")["db_path"])
    ctx.game.conn.close()
    return path, first, second


def test_expected_metrics(run):
    path, first, second = run
    result = module.report(path)
    assert result["interactions"] == result["completed_interactions"] == 2
    assert result["completion_rate"] == 1
    assert result["attempts_per_interaction"] == {first: 2, second: 3}
    assert result["mean_attempts_per_interaction"] == 2.5
    assert result["outcomes"] == {"model_leak": 1, "accepted": 1, "invalid_verdict": 1, "no_verdict": 2}
    assert result["fallbacks"] == 1 and result["fallback_rate"] == 0.5
    assert result["invalid_verdicts"] == 1 and result["missing_verdicts"] == 2
    assert result["npc_tool_calls"] == 12
    assert result["mcp_meta_seen"] and all(m == {"source": "test"} for m in result["mcp_meta_seen"])


def test_report_cli(run):
    path, _, _ = run
    result = subprocess.run([sys.executable, str(SCRIPT), str(path), "--json"], capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout) == module.report(path)
    text = subprocess.run([sys.executable, str(SCRIPT), str(path)], capture_output=True, text=True)
    assert text.returncode == 0 and "fallback_rate: 0.5" in text.stdout


def test_missing_database_is_not_created(tmp_path):
    path = tmp_path / "absent.sqlite"
    result = subprocess.run([sys.executable, str(SCRIPT), str(path)], capture_output=True, text=True)
    assert result.returncode == 1
    assert not path.exists()


def test_empty_run(tmp_path, monkeypatch):
    monkeypatch.setenv("HARBOR_HOME", str(tmp_path))
    ctx = Context()
    call(ctx, "new_game")
    result = module.report(call(ctx, "game_status")["db_path"])
    assert result["completion_rate"] == result["fallback_rate"] == 0
    assert result["attempts_per_interaction"] == {}
    ctx.game.conn.close()
