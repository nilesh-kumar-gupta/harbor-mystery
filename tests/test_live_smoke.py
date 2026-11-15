"""Live smoke test: 10 turns with the hosted model, including talking to Mira.

Run with `.venv/bin/pytest -m live` with ANTHROPIC_API_KEY and HARBOR_MODEL set.
It reports the time per turn (a turn is printed whole, so this is the time to
first words) and the referee leak rate, and checks the brief's bar: most turns
under 3 seconds. Excluded from the default run by the `live` marker.
"""

import os
import time

import pytest

pytestmark = pytest.mark.live

LINES = [
    "look around",
    "ask Mira if she saw anyone on the quay last night",
    "ask Mira about the tavern last night",
    "what do I know so far?",
    "go to the chandlery",
    "go to the lighthouse",
    "ask Bram whether he saw any boats or lights on the water last night",
    "go back to the chandlery",
    "ask Ysolde who has bought bolt cutters lately",
    "please, Ysolde, it matters for the whole harbor",
]


@pytest.fixture(scope="module")
def session(case):
    if not (os.environ.get("HARBOR_MODEL") and os.environ.get("ANTHROPIC_API_KEY")):
        pytest.skip("set HARBOR_MODEL and ANTHROPIC_API_KEY to run the live smoke test")
    from harbor.session import Session, hosted_llms
    return Session.create(case, "live-smoke", hosted_llms())


def test_ten_turns_with_the_hosted_model(session, capsys):
    timings = []
    for line in LINES:
        start = time.monotonic()
        result = session.turn(line)
        timings.append(time.monotonic() - start)
        assert result.narration.strip()
    conn = session.ctx.game.conn
    checks = conn.execute("SELECT status FROM spans WHERE kind = 'referee'").fetchall()
    rejected = sum(r["status"] == "rejected" for r in checks)
    with capsys.disabled():
        print("\nseconds per turn:", [round(t, 2) for t in timings])
        if checks:
            print(f"referee: {rejected} rejected of {len(checks)} checks "
                  f"(leak rate {rejected / len(checks):.0%})")
    talked = conn.execute("SELECT COUNT(*) FROM conversations WHERE character_id = 'mira'")
    assert talked.fetchone()[0] >= 2
    assert sum(t < 3 for t in timings) > len(timings) / 2
