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
