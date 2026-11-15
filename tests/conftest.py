from pathlib import Path

import pytest

from harbor.engine import state
from harbor.engine.case_loader import load_case
from harbor.tools import REGISTRY, Context

CASE_PATH = Path(__file__).parent.parent / "cases" / "saltmere.json"


@pytest.fixture(scope="session")
def case():
    return load_case(CASE_PATH)


@pytest.fixture
def ctx(case):
    """A started in-memory run with no model attached."""
    context = Context(game=state.new_game(case, "test-run", seed=1))
    assert REGISTRY.call(context, "start_run", {}, "engine")["ok"]
    return context


@pytest.fixture
def call(ctx):
    """Call a game master tool on the fixture run."""
    def _call(name, **arguments):
        return REGISTRY.call(ctx, name, arguments, "gm")
    return _call


@pytest.fixture
def make_session(case):
    """Build an in-memory Session; `llms` overrides any of the offline stand-ins."""
    from harbor.offline import offline_llms
    from harbor.session import Session

    def _make(run_id="session-run", seed=1, **llms):
        return Session.create(case, run_id, {**offline_llms(), **llms}, seed=seed)
    return _make


def tool(ctx, name, **arguments):
    return REGISTRY.call(ctx, name, arguments, "gm")


def talk_call(ctx, arguments, audience="gm"):
    from harbor.agents.conversation import run
    if audience != "gm":
        return {"ok": False, "error": "talk requires gm audience"}
    return run(ctx, getattr(ctx, "test_npc_llm", None),
               getattr(ctx, "test_referee_llm", None), **arguments)


def active_ids(ctx):
    row = ctx.game.conn.execute("SELECT * FROM interactions ORDER BY rowid DESC LIMIT 1").fetchone()
    return {"interaction_id": row["interaction_id"], "attempt": row["attempt"]} if row else {"interaction_id": "missing", "attempt": 1}


def npc_call(ctx, name, **args):
    return REGISTRY.call(ctx, name, {**args, **active_ids(ctx)}, "npc")


def open_npc(ctx, character=None, message="hello"):
    from harbor.engine import interaction, state
    current = interaction.opened(ctx.game)
    if current is None:
        character = character or state.read(ctx.game)["active_character"]
        begin = REGISTRY.call(ctx, "begin_talk", {"character": character, "message": message}, "gm")
        assert begin["ok"], begin
    return REGISTRY.call(ctx, "npc_context", active_ids(ctx), "npc")


def inspected_talk(ctx, character, message, name="recall_knowledge"):
    from harbor.llm.scripted import ScriptedLLM
    from harbor.agents.conversation import run
    import json
    captured = []
    def policy(system, messages, tools):
        if messages[-1]["role"] != "tool":
            return {"tool_calls": [(name, {})]}
        captured.append(json.loads(messages[-1]["content"]))
        return "Hm."
    out = run(ctx, ScriptedLLM(policy), None, character, message)
    assert out["ok"]
    return captured[-1]
