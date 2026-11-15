"""Drive the same public lifecycle the plugin uses, with offline models."""
from harbor.agents import npc, checker
from harbor.tools import REGISTRY


def run(ctx, npc_llm, referee_llm, character, message):
    if npc_llm is None:
        return {"ok": False, "error": "no NPC agent is configured"}
    start = REGISTRY.call(ctx, "begin_talk", {"character": character, "message": message}, "gm")
    if not start["ok"]:
        return start
    iid, attempt = start["interaction_id"], 1
    while True:
        ids = {"interaction_id": iid, "attempt": attempt}
        card = REGISTRY.call(ctx, "npc_context", ids, "npc")
        sid = f"{iid}-a{attempt}-npc"
        text = ""
        try:
            out = npc.run(ctx, npc_llm, card, iid, attempt)
            if out.capped:
                ctx.limited.add((iid, attempt))
            else:
                text = out.text.strip()
            ctx.tracer._virtual[sid].output = {"text": out.text, "capped": out.capped}
        except Exception as exc:
            ctx.tracer._virtual[sid].attrs["error.type"] = type(exc).__name__
            ctx.tracer._virtual[sid].output = {"error": str(exc), "text": ""}
        if text:
            REGISTRY.call(ctx, "submit_draft", {**ids, "text": text}, "npc")
            # A code rejection needs no model handoff. This read-only tool view
            # keeps the orchestrator independent of engine modules.
            check = REGISTRY.call(ctx, "referee_context", ids, "referee")
            if check.get("ok"):
                if ctx.tracer._virtual[f"{iid}-a{attempt}-ref"].attrs["referee.code_check"] == "pass":
                    checker.run(ctx, referee_llm, ids)
        result = REGISTRY.call(ctx, "finalize_talk", {"interaction_id": iid}, "gm")
        if result.get("status") != "retry":
            if not result.get("ok"):
                return result
            out = {k: result[k] for k in ("ok", "reply", "new_clues", "clock")}
            out["character"] = start["character"]
            if result.get("fallback"):
                out["fallback"] = True
            return out
        attempt = result["attempt"]
