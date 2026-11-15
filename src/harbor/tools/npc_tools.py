"""The NPC agent's tools. None takes a character: each acts on
game_state.active_character, which only the `talk` tool sets."""

from __future__ import annotations

from harbor.engine import rules, state
from harbor.tools.registry import REGISTRY, Context, error


def _active(ctx: Context) -> str | None:
    return state.read(ctx.game)["active_character"]


NO_ACTIVE = "no conversation is in progress"


@REGISTRY.tool("recall_knowledge", "npc",
               "What you know that you can tell the player now: clues (id, text) and "
               "pointers to people who know more. `guarded` means you are holding something "
               "back until you trust the player more.")
def recall_knowledge(ctx: Context) -> dict:
    who = _active(ctx)
    if who is None:
        return error(NO_ACTIVE)
    return {"ok": True, **rules.recall(ctx.game, who)}


@REGISTRY.tool("recall_conversation", "npc", "Your earlier lines with the player today.")
def recall_conversation(ctx: Context) -> dict:
    who = _active(ctx)
    if who is None:
        return error(NO_ACTIVE)
    return {"ok": True, "lines": [
        {"turn": r["turn"], "speaker": r["speaker"], "text": r["text"]}
        for r in state.conversation(ctx.game, who)]}


@REGISTRY.tool("reveal_clue", "npc",
               "Tell the player a clue you know. This is the only way a clue reaches the "
               "player's notebook; call it before stating the clue.",
               {"clue_id": {"type": "string", "description": "A clue id from recall_knowledge"}},
               required=("clue_id",))
def reveal_clue(ctx: Context, clue_id: str) -> dict:
    who = _active(ctx)
    if who is None:
        return error(NO_ACTIVE)
    problem = rules.check_reveal(ctx.game, who, clue_id)
    if problem:
        return error(problem)
    new = state.add_clue(ctx.game, clue_id, who)
    out = {"ok": True, "id": clue_id, "text": rules.clue(ctx.game, clue_id)["text"]}
    if not new:
        out["already_known"] = True
    return out


@REGISTRY.tool("update_trust", "npc",
               "Report how the player is treating you. The engine decides whether your "
               "trust in them changes.",
               {"reason": {"type": "string", "enum": list(rules.TRUST_REASONS)}},
               required=("reason",))
def update_trust(ctx: Context, reason: str) -> dict:
    who = _active(ctx)
    if who is None:
        return error(NO_ACTIVE)
    level, clue_id, why_not = rules.trust_step(ctx.game, who, reason)
    if level is None:
        return error(why_not, trust=state.trust_of(ctx.game, who))
    state.change_trust(ctx.game, who, level, reason, clue_id)
    return {"ok": True, "trust": level}
