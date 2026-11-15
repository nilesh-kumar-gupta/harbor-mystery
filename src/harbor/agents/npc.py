"""The NPC agent: its own prompt, its own four tools, at most 4 tool calls per reply.

It sees the character card (persona and speech) and the player's message, and
nothing else about the case. Its tools act on the active character only.
"""

from __future__ import annotations

from harbor import prompts
from harbor.agents.loop import run_loop
from harbor.llm.base import LLMClient
from harbor.tools import REGISTRY, Context

MAX_TOOL_CALLS = 4


def system_prompt(card: dict) -> str:
    return (prompts.load("npc") + "\n\n"
            f"You are {card['name']}, the {card['role']}.\n"
            f"Persona: {card['persona']}\nSpeech style: {card['speech_style']}\n")


def run(ctx: Context, llm: LLMClient, card: dict, interaction_id: str, attempt: int):
    """Only the four gameplay tools are offered; bind ids outside the model."""
    content = f"The player says: {card['player_message']}"
    if card.get("note"):
        content += f"\n\n(Note: {card['note']})"
    schemas = []
    for name in ("recall_knowledge", "recall_conversation", "reveal_clue", "update_trust"):
        tool = REGISTRY.get(name)
        schema = tool.input_schema
        schemas.append({"name": name, "description": tool.description, "input_schema": {
            **schema, "properties": {k: v for k, v in schema["properties"].items()
                                      if k not in ("interaction_id", "attempt")},
            "required": [k for k in schema["required"] if k not in ("interaction_id", "attempt")]}})
    return run_loop(llm, ctx.tracer, f"npc:{card['id']}", system_prompt(card),
                    [{"role": "user", "content": content}], schemas,
                    lambda name, args: REGISTRY.call(ctx, name,
                        {**args, "interaction_id": interaction_id, "attempt": attempt}, "npc"),
                    MAX_TOOL_CALLS, attrs={"game.npc": card["id"],
                        "game.interaction_id": interaction_id, "game.attempt": attempt},
                    parent_id=f"{interaction_id}-a{attempt}-npc")
