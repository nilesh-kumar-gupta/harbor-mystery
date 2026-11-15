"""The offline semantic referee; it never runs with the engine write guard open."""
import json
from harbor import prompts, referee
from harbor.tools import REGISTRY
from harbor.agents.loop import _ask


def run(ctx, llm, ids):
    result = REGISTRY.call(ctx, "referee_context", ids, "referee")
    if not result["ok"]:
        return
    parent = f"{ids['interaction_id']}-a{ids['attempt']}-ref"
    answer, reason = "OK", ""
    try:
        if llm is not None:
            reply = _ask(llm, ctx.tracer, "referee", prompts.load("referee"),
                         [{"role": "user", "content": json.dumps({k: v for k, v in result.items() if k != 'ok'})}], [], {"game.interaction_id": ids["interaction_id"], "game.attempt": ids["attempt"]},
                         parent_id=parent)
            answer = reply.text
    except Exception as exc:
        answer, reason = None, f"model_error: {type(exc).__name__}"
    REGISTRY.call(ctx, "submit_verdict", {**ids, "verdict": answer, "reason": reason}, "referee")
    from harbor.tools.talk_tools import virtual
    virtual(ctx, ids["interaction_id"], ids["attempt"], "referee").attrs["referee.verdict_source"] = "offline"
