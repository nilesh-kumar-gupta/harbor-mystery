"""The one hand-written agent loop, shared by the game master and the NPCs.

Ask the model; run the tool calls it asks for; feed the results back; stop
when it answers without tools or when the tool-call cap would be exceeded.
Every model call is an llm_call span. Tool calls are spans already (the
registry opens them), so this loop never runs a tool or a model untraced.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Callable

from harbor.llm.base import LLMClient, LLMReply


@dataclass
class LoopResult:
    text: str
    capped: bool = False                     # the model wanted more tool calls than allowed
    tool_results: list[tuple[str, dict, dict]] = field(default_factory=list)  # name, args, result
    messages: list[dict] = field(default_factory=list)


def run_loop(llm: LLMClient, tracer, agent_name: str, system: str, messages: list[dict],
             tools: list[dict], execute: Callable[[str, dict], dict], max_tool_calls: int,
             attrs: dict | None = None) -> LoopResult:
    """Run one agent to a final answer. `execute(name, arguments)` runs a tool and
    returns its JSON result; it must never raise (the registry guarantees that)."""
    messages = list(messages)
    result = LoopResult(text="", messages=messages)
    used = 0
    for _ in range(max_tool_calls + 1):   # each round uses a call, so this always ends
        reply = _ask(llm, tracer, agent_name, system, messages, tools, attrs or {})
        messages.append(reply.as_message())
        if not reply.tool_calls:
            result.text = reply.text
            return result
        if used + len(reply.tool_calls) > max_tool_calls:
            result.capped = True
            result.text = reply.text
            return result
        for call in reply.tool_calls:
            used += 1
            output = execute(call.name, call.arguments)
            result.tool_results.append((call.name, call.arguments, output))
            messages.append({"role": "tool", "tool_call_id": call.id, "name": call.name,
                             "content": json.dumps(output)})
    result.capped = True
    return result


def _ask(llm, tracer, agent_name, system, messages, tools, attrs) -> LLMReply:
    span_attrs = {"gen_ai.operation.name": "chat", "gen_ai.agent.name": agent_name,
                  "gen_ai.request.model": getattr(llm, "model", ""), **attrs}
    with tracer.span("llm_call", "chat", attrs=span_attrs,
                     input={"system": system, "messages": messages,
                            "tools": [t["name"] for t in tools]}) as span:
        reply = llm.complete(system, messages, tools)
        span.model = reply.model
        span.input_tokens, span.output_tokens = reply.input_tokens, reply.output_tokens
        span.attrs["gen_ai.usage.input_tokens"] = reply.input_tokens
        span.attrs["gen_ai.usage.output_tokens"] = reply.output_tokens
        span.output = {"text": reply.text, "tool_calls": [
            {"id": c.id, "name": c.name, "arguments": c.arguments} for c in reply.tool_calls]}
        return reply
