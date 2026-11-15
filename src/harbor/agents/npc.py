"""The NPC agent: its own prompt, its own four tools, at most 4 tool calls per reply.

It sees the character card (persona and speech) and the player's message, and
nothing else about the case. Its tools act on the active character only.
"""

from __future__ import annotations

from harbor.agents import prompts
from harbor.agents.loop import run_loop
from harbor.llm.base import LLMClient
from harbor.tools import REGISTRY, Context, Draft

MAX_TOOL_CALLS = 4


def system_prompt(card: dict) -> str:
    return (prompts.load("npc") + "\n\n"
            f"You are {card['name']}, the {card['role']}.\n"
            f"Persona: {card['persona']}\nSpeech style: {card['speech_style']}\n")


def make_npc_runner(llm: LLMClient):
    """Build the runner the `talk` tool calls: (ctx, card, message, note) -> Draft."""
    def run(ctx: Context, card: dict, message: str, note: str | None) -> Draft:
        content = f"The player says: {message}"
        if note:
            content += f"\n\n(Note: {note})"
        out = run_loop(
            llm, ctx.tracer, f"npc:{card['id']}", system_prompt(card),
            [{"role": "user", "content": content}], REGISTRY.schemas("npc"),
            lambda name, args: REGISTRY.call(ctx, name, args, "npc"),
            MAX_TOOL_CALLS, attrs={"game.npc": card["id"]},
        )
        return Draft(text=out.text, capped=out.capped)
    return run
