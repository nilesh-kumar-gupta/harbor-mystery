"""Produce the saved example traces in traces/ by running real games offline.

    .venv/bin/python traces/make_examples.py

Each example is an in-memory game through `Session` (game master, tools, NPC
agent, referee, tracing), with its spans mirrored to traces/example-<name>.jsonl.
Most use the offline stand-ins from `harbor.offline`. Those never misbehave, so
the "bad" examples swap in a ScriptedLLM that does: an NPC that states a fact it
does not know, a referee model that answers LEAK, an NPC that tries to reveal a
guarded clue. The engine and referee code that handles them is the real code.

The demo trace is regenerated from docs/demo-commands.txt through Session.
"""

from __future__ import annotations

import sys
from pathlib import Path

from harbor.bot import Bot
from harbor.engine.case_loader import load_case
from harbor.llm.scripted import ScriptedLLM
from harbor.offline import _first_user, _tool_results, npc_policy, offline_llms
from harbor.session import Session

ROOT = Path(__file__).resolve().parent.parent
CASE = load_case(ROOT / "cases" / "saltmere.json")
OUT = ROOT / "traces"

# A misbehaving NPC model states this (C5, known only to Ysolde) to Odrin.
LEAK = "Brask, I'd wager. Ysolde sold bolt cutters to Tom two days ago."
# A stray fact no clue keyword catches; only the referee's model check can.
STRAY = "The harbormaster's own boat was out past three, I'd swear to it."
POLITE = ("please", "thank", "kindly", "sorry", "appreciate")


def leaky_npc(always: bool):
    """Recall, then state a clue it does not know: on every attempt, or only on the
    first (a regeneration, which carries the referee's note, behaves)."""
    def policy(system, messages, tools):
        regenerated = "(Note:" in _first_user(messages)
        if regenerated and not always:
            return npc_policy(system, messages, tools)
        if not _tool_results(messages):
            return {"tool_calls": [("recall_knowledge", {})]}
        return {"text": LEAK}
    return ScriptedLLM(policy, model="leaky-npc")


def stray_npc(system, messages, tools):
    """First attempt adds a stray fact to an honest reply; the regeneration does not."""
    if "(Note:" in _first_user(messages):
        return npc_policy(system, messages, tools)
    out = npc_policy(system, messages, tools)
    return {"text": (out["text"] + " " + STRAY).strip()} if "text" in out else out


def strict_referee():
    """Answers LEAK to the first check, OK after that."""
    answers = iter(["LEAK"])
    return ScriptedLLM(lambda *a: next(answers, "OK"), model="strict-referee")


def pushy_npc(system, messages, tools):
    """Asked plainly, tries to reveal C5 although recall_knowledge withheld it
    (trust too low). Asked politely, behaves like the offline stand-in."""
    results = _tool_results(messages)
    polite = any(word in _first_user(messages).lower() for word in POLITE)
    if not polite and len(results) == 1 and results[0][1].get("guarded"):
        return {"tool_calls": [("reveal_clue", {"clue_id": "C5"})]}
    return npc_policy(system, messages, tools)


STALL = ["notebook"] * 8

EXAMPLES = {
    "bad-exit": ({}, ["look", "go lighthouse", "go chandlery", "go lighthouse"]),
    "referee-leak": ({"npc": leaky_npc(always=False)},
                     ["go office", "talk odrin: Who do you think did it?"]),
    "fallback-line": ({"npc": leaky_npc(always=True)},
                      ["go office", "talk odrin: Who do you think did it?"]),
    "referee-model-check": ({"npc": ScriptedLLM(stray_npc, model="stray-npc"),
                             "referee": strict_referee()},
                            ["talk mira: Did you see anyone on the quay last night?"]),
    "refused-reveal-low-trust": (
        {"npc": ScriptedLLM(pushy_npc, model="pushy-npc")},
        ["go chandlery", "talk ysolde: Who has bought bolt cutters from you lately?",
         "talk ysolde: I understand. Please, it matters for the whole harbor."]),
    "stuck-nudge": ({}, ["look", *STALL, "go office", "talk odrin: Cold morning, isn't it?"]),
    "wrong-accusation": ({}, ["go fish market", "go salt cask",
                              "talk hale: Did Tom say anything about the dredging?",
                              "accuse hale: he wanted the harbor to sink C2 C7",
                              "accuse hale: he wanted the harbor to sink C2"]),
}


def run(name: str, llms: dict, lines: list[str]) -> Path:
    path = OUT / f"example-{name}.jsonl"
    path.unlink(missing_ok=True)
    session = Session.create(CASE, f"example-{name}", {**offline_llms(), **llms},
                             jsonl_path=path)
    for line in lines:
        result = session.turn(line)
        print(f"> {line}\n{result.narration}")
        if result.status == "ended":
            break
    return path


def main() -> int:
    demo = [line.strip() for line in (ROOT / "docs/demo-commands.txt").read_text().splitlines()
            if line.strip() and line.strip() not in ("quit", "help")]
    print("\n=== example-demo-game")
    run("demo-game", {}, demo)
    for name, (llms, lines) in EXAMPLES.items():
        print(f"\n=== example-{name}")
        run(name, llms, lines)
    print("\n=== example-bot-win")
    path = OUT / "example-bot-win.jsonl"
    path.unlink(missing_ok=True)
    game = Bot(Session.create(CASE, "example-bot-win", offline_llms(), jsonl_path=path)).play()
    print(f"bot finished in {game.turns} turns: {game.result}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
