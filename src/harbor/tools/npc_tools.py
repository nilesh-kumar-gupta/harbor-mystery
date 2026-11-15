"""The four NPC tools operate only on the current interaction and attempt."""
from harbor.engine import interaction, rules, state
from harbor.tools.registry import REGISTRY, error
from harbor.tools.talk_tools import IDS, REQUIRED


def read(ctx, iid, attempt, fn):
    problem = interaction.validate(ctx.game, iid, attempt, ("drafting",))
    if problem:
        return error(problem)
    row = interaction.get(ctx.game, iid)
    with interaction.overlay(ctx.game, interaction.attempt_row(ctx.game, iid, attempt)):
        return fn(row["character_id"])


@REGISTRY.tool("recall_knowledge", "npc", "Recall your unlocked clues and pointers.", IDS, REQUIRED)
def recall_knowledge(ctx, interaction_id, attempt):
    return read(ctx, interaction_id, attempt, lambda who: {"ok": True, **rules.recall(ctx.game, who)})


@REGISTRY.tool("recall_conversation", "npc", "Recall your conversation history.", IDS, REQUIRED)
def recall_conversation(ctx, interaction_id, attempt):
    return read(ctx, interaction_id, attempt, lambda who: {"ok": True, "lines": [
        {k: r[k] for k in ("turn", "speaker", "text")} for r in state.conversation(ctx.game, who)]})


@REGISTRY.tool("reveal_clue", "npc", "Stage a clue you know for the player.",
               {**IDS, "clue_id": {"type": "string"}}, (*REQUIRED, "clue_id"))
def reveal_clue(ctx, interaction_id, attempt, clue_id):
    result = interaction.stage(ctx.game, interaction_id, attempt, "reveal_clue", {"clue_id": clue_id})
    if result["ok"]:
        result.update(id=clue_id, text=rules.clue(ctx.game, clue_id)["text"])
    return result


@REGISTRY.tool("update_trust", "npc", "Stage an engine-validated trust step.",
               {**IDS, "reason": {"type": "string", "enum": list(rules.TRUST_REASONS)}}, (*REQUIRED, "reason"))
def update_trust(ctx, interaction_id, attempt, reason):
    result = interaction.stage(ctx.game, interaction_id, attempt, "update_trust", {"reason": reason})
    if result["ok"]:
        result.update(read(ctx, interaction_id, attempt, lambda who: {"trust": state.trust_of(ctx.game, who)}))
    elif result.get("error") not in ("unknown interaction", "stale interaction", "wrong attempt", "wrong interaction state"):
        row = interaction.get(ctx.game, interaction_id)
        if row:
            trust = read(ctx, interaction_id, attempt, lambda who: state.trust_of(ctx.game, who))
            if type(trust) is int:
                result["trust"] = trust
    return result
