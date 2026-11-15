"""Durable conversation tools. Each overlay ends before a tool returns."""
import json

from harbor import referee
from harbor.engine import interaction, rules, state
from harbor.tools.registry import REGISTRY, error

IDS = {"interaction_id": {"type": "string"}, "attempt": {"type": "integer"}}
REQUIRED = ("interaction_id", "attempt")


def correlation(ctx, iid, attempt):
    row = interaction.get(ctx.game, iid)
    return {"game.interaction_id": iid, "game.attempt": attempt,
            "game.npc": row["character_id"], "game.turn": row["turn"]}


def virtual(ctx, iid, attempt, role):
    row = interaction.get(ctx.game, iid)
    sid = f"{iid}-a{attempt}-{'npc' if role == 'npc' else 'ref'}"
    return ctx.tracer.open_virtual(
        "agent" if role == "npc" else "referee",
        f"npc:{row['character_id']}" if role == "npc" else "referee", sid,
        parent_id=f"{ctx.game.run_id}-t{row['turn']:04d}-gm",
        trace_id=f"{ctx.game.run_id}-t{row['turn']:04d}",
        input=row["player_message"], attrs=correlation(ctx, iid, attempt))


@REGISTRY.tool("begin_talk", "gm", "Begin a conversation. Costs 10 minutes.",
               {"character": {"type": "string"}, "message": {"type": "string"}},
               required=("character", "message"))
def begin_talk(ctx, character, message):
    game = ctx.game
    present = [r["name"] for r in rules.present(game, state.read(game)["place"])]
    card = rules.resolve_character(game, character)
    if card is None:
        return error(f"unknown character {character!r}", present=present)
    if rules.character_place(game, card["id"]) != state.read(game)["place"]:
        return error(f"{card['name']} is not here", present=present)
    if not message.strip():
        return error("message is empty")
    return {**interaction.begin(game, character, message),
            "npc_agent": "harbor-mystery:npc", "referee_agent": "harbor-mystery:referee"}


@REGISTRY.tool("npc_context", "npc", "Fetch your character card.", IDS, REQUIRED)
def npc_context(ctx, interaction_id, attempt):
    result = interaction.start_npc(ctx.game, interaction_id, attempt)
    if not result["ok"]:
        return result
    row = interaction.get(ctx.game, interaction_id)
    card = rules.resolve_character(ctx.game, row["character_id"])
    virtual(ctx, interaction_id, attempt, "npc")
    return {"ok": True, **{k: card[k] for k in ("id", "name", "role", "persona", "speech_style")},
            "player_message": row["player_message"],
            "note": interaction.attempt_row(ctx.game, interaction_id, attempt)["note"],
            "calls_left": max(0, 4 - ctx.counts.get((interaction_id, attempt), 0))}


@REGISTRY.tool("submit_draft", "npc", "Submit your reply for evaluation.",
               {**IDS, "text": {"type": "string"}}, (*REQUIRED, "text"))
def submit_draft(ctx, interaction_id, attempt, text):
    result = interaction.submit_draft(ctx.game, interaction_id, attempt, text)
    if result["ok"]:
        span = virtual(ctx, interaction_id, attempt, "npc")
        span.output = {"text": text, "capped": (interaction_id, attempt) in ctx.limited}
    return result


@REGISTRY.tool("referee_context", "referee", "Fetch the reply and permitted facts.", IDS, REQUIRED)
def referee_context(ctx, interaction_id, attempt):
    game = ctx.game
    problem = interaction.validate(game, interaction_id, attempt, ("evaluating",))
    if problem:
        return error(problem)
    row = interaction.get(game, interaction_id)
    draft = interaction.attempt_row(game, interaction_id, attempt)
    virtual(ctx, interaction_id, attempt, "referee")
    with interaction.overlay(game, draft):
        verdict = referee.code_check(game, row["character_id"], row["player_message"], draft["draft"])
        virtual(ctx, interaction_id, attempt, "referee").attrs["referee.code_check"] = "pass" if verdict.ok else "leak"
        return {"ok": True, **referee.referee_context(game, rules.resolve_character(game, row["character_id"]),
                                                     row["player_message"], draft["draft"])}


@REGISTRY.tool("submit_verdict", "referee", "Record OK or LEAK; any other value vetoes.",
               {**IDS, "verdict": {}, "reason": {"type": "string"}}, (*REQUIRED, "verdict"))
def submit_verdict(ctx, interaction_id, attempt, verdict, reason=""):
    parsed = referee.parse_verdict(verdict)
    if parsed == "INVALID" and not reason.startswith("model_error:"):
        reason = f"invalid verdict: {str(verdict)[:200]}" + (f"; {reason}" if reason else "")
    result = interaction.submit_verdict(ctx.game, interaction_id, attempt, parsed, reason)
    if result["ok"]:
        span = virtual(ctx, interaction_id, attempt, "referee")
        span.attrs.update({"referee.verdict": parsed, "referee.verdict_source": "mcp",
                           "referee.model_error": reason.startswith("model_error:")})
    return result


@REGISTRY.tool("finalize_talk", "gm", "Validate and commit, or retry the conversation.",
               {"interaction_id": {"type": "string"}}, ("interaction_id",), when_ended=True)
def finalize_talk(ctx, interaction_id):
    game = ctx.game
    row = interaction.get(game, interaction_id)
    if row is None or row["status"] in ("committed", "abandoned"):
        return interaction.finalize(game, interaction_id, referee.code_check)
    attempt = row["attempt"]
    first = game.conn.execute("SELECT COALESCE(MAX(seq),0) FROM events WHERE run_id=?", (game.run_id,)).fetchone()[0] + 1
    result = interaction.finalize(game, interaction_id, referee.code_check,
                                  tool_limit=(interaction_id, attempt) in ctx.limited)
    if not result["ok"]:
        return result
    draft = interaction.attempt_row(game, interaction_id, attempt)
    outcome = draft["outcome"]
    current = ctx.tracer.current()
    current.attrs.update(correlation(ctx, interaction_id, attempt))
    last = game.conn.execute("SELECT MAX(seq) FROM events WHERE run_id=?", (game.run_id,)).fetchone()[0]
    current.attrs.update({"game.outcome": outcome, "game.event_seq_start": first, "game.event_seq_end": last})
    ref = virtual(ctx, interaction_id, attempt, "referee")
    model_error = (draft["verdict_reason"] or "").startswith("model_error:")
    code = "leak" if outcome == "code_leak" else "pass" if draft["draft"] else "skipped: no draft"
    ref.attrs.update({"referee.code_check": code, "referee.verdict": draft["verdict"] or "missing",
                      "referee.model_error": model_error, "game.leaked_clues": json.loads(draft["leaked"] or "[]")})
    ref.attrs.setdefault("referee.verdict_source", "mcp")
    ref.attrs["referee.model_check"] = ("skipped: code check failed" if code == "leak" else
        "error" if model_error else "pass" if draft["verdict"] == "OK" else "leak" if draft["verdict"] == "LEAK" else "invalid" if draft["verdict"] else "missing")
    status = "error" if model_error else "ok" if outcome == "accepted" else "rejected"
    ctx.tracer.close_virtual(ref.span_id, status=status, output={"outcome": outcome})
    npc = virtual(ctx, interaction_id, attempt, "npc")
    ctx.tracer.close_virtual(npc.span_id, status="ok" if outcome == "accepted" else "rejected", output=npc.output)
    return result
