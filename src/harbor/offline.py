"""Deterministic model stand-ins, so the whole agent architecture runs offline.

These are ScriptedLLM policies, not game logic: each reads only what a real
model would see (its messages and tool results) and picks tool calls.

- `gm_policy`: a game master that understands the typed commands in
  `commands.py` and narrates by rendering tool results.
- `npc_policy`: an NPC that recalls, asks the engine to raise trust when it
  holds something back (the engine allows a repeated ask or a shown clue),
  reveals what is unlocked and repeats the engine's pointer lines.
- `referee_policy`: a stray-fact checker that always answers OK (the code check
  still runs in full).
"""

from __future__ import annotations

import json

from harbor.commands import parse_command, render
from harbor.llm.scripted import ScriptedLLM

def _tool_results(messages: list[dict]) -> list[tuple[str, dict]]:
    return [(m["name"], json.loads(m["content"])) for m in messages if m["role"] == "tool"]


def _first_user(messages: list[dict]) -> str:
    return next(m["content"] for m in messages if m["role"] == "user")


def gm_policy(system: str, messages: list[dict], tools: list[dict]) -> dict:
    results = _tool_results(messages)
    if results:
        return {"text": "\n".join(render(name, result) for name, result in results)}
    line = _first_user(messages).split("\n\nState:", 1)[0].removeprefix("Player: ")
    command = parse_command(line)
    if command is None:
        return {"text": "(not a command)"}
    return {"tool_calls": [command]}


def npc_policy(system: str, messages: list[dict], tools: list[dict]) -> dict:
    results = _tool_results(messages)
    used = len(results)
    recalls = [r for name, r in results if name == "recall_knowledge" and r.get("ok")]
    revealed = [r for name, r in results if name == "reveal_clue" and r.get("ok")]
    trusted = [r for name, r in results if name == "update_trust"]
    tried = set(_revealed_ids(messages))
    if not recalls:
        return {"tool_calls": [("recall_knowledge", {})]}
    latest = recalls[-1]
    if not trusted and latest.get("guarded") and used <= 2:
        if latest.get("shown"):
            return {"tool_calls": [("update_trust", {"reason": "related_clue_shown"})]}
        return {"tool_calls": [("update_trust", {"reason": "polite_repeat"})]}
    if trusted and trusted[-1].get("ok") and results[-1][0] == "update_trust":
        return {"tool_calls": [("recall_knowledge", {})]}
    for clue in latest["clues"]:
        if clue["id"] not in tried and not clue.get("in_notebook") and used < 4:
            return {"tool_calls": [("reveal_clue", {"clue_id": clue["id"]})]}
    lines = [r["text"] for r in revealed] + [p["line"] or f"Ask {p['ask']}."
                                             for p in latest["pointers"]]
    return {"text": " ".join(lines)}


def _revealed_ids(messages: list[dict]) -> list[str]:
    ids = []
    for m in messages:
        for call in m.get("tool_calls", ()) if m["role"] == "assistant" else ():
            if call["name"] == "reveal_clue":
                ids.append(call["arguments"].get("clue_id"))
    return ids


def referee_policy(system: str, messages: list[dict], tools: list[dict]) -> str:
    return "OK"


def offline_llms() -> dict[str, ScriptedLLM]:
    return {"gm": ScriptedLLM(gm_policy, model="offline-gm"),
            "npc": ScriptedLLM(npc_policy, model="offline-npc"),
            "referee": ScriptedLLM(referee_policy, model="offline-referee")}
