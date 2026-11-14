"""ScriptedLLM: a deterministic stand-in for a model, used by every offline test.

The script is either a list of replies handed out in order, or a function
`(system, messages, tools) -> reply` that decides each time. A reply may be an
LLMReply, a string (text only), or a dict {"text": ..., "tool_calls": [(name, arguments), ...]}.
"""

from __future__ import annotations

from typing import Any, Callable, Sequence

from harbor.llm.base import LLMClient, LLMReply, ToolCall


class ScriptExhausted(RuntimeError):
    """The scripted replies ran out: the code under test asked for more than was scripted."""


class ScriptedLLM(LLMClient):
    def __init__(self, script: Sequence[Any] | Callable[..., Any], model: str = "scripted") -> None:
        self.model = model
        self._policy = script if callable(script) else None
        self._queue = None if callable(script) else list(script)
        self.calls: list[dict] = []  # what each call was given, for assertions

    def complete(self, system: str, messages: list[dict], tools: list[dict]) -> LLMReply:
        self.calls.append({"system": system, "messages": [dict(m) for m in messages],
                           "tools": [t["name"] for t in tools]})
        if self._policy is not None:
            item = self._policy(system, messages, tools)
        elif self._queue:
            item = self._queue.pop(0)
        else:
            raise ScriptExhausted(f"no scripted reply left for call {len(self.calls)}")
        return self._reply(item, len(self.calls))

    @property
    def remaining(self) -> int:
        return len(self._queue or [])

    def _reply(self, item: Any, n: int) -> LLMReply:
        if isinstance(item, LLMReply):
            reply = item
        elif isinstance(item, str):
            reply = LLMReply(text=item)
        else:
            calls = [c if isinstance(c, ToolCall) else ToolCall(f"call_{n}_{i}", c[0], dict(c[1]))
                     for i, c in enumerate(item.get("tool_calls", ()))]
            reply = LLMReply(text=item.get("text", ""), tool_calls=calls)
        reply.model = reply.model or self.model
        return reply
