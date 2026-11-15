"""HostedLLM: the Anthropic Messages API with tool use.

The model name comes from HARBOR_MODEL and the key from ANTHROPIC_API_KEY (or
any credential the SDK resolves by itself). `anthropic` is imported here only,
and only when a client is built, so the rest of the game runs without it.
Not exercised by the offline tests; see tests/test_live_smoke.py.
"""

from __future__ import annotations

import json
import os

from harbor.llm.base import LLMClient, LLMReply, ToolCall

MAX_TOKENS = 4096


class HostedLLMError(RuntimeError):
    pass


class HostedLLM(LLMClient):
    def __init__(self, model: str | None = None, max_tokens: int = MAX_TOKENS,
                 client=None) -> None:
        self.model = model or os.environ.get("HARBOR_MODEL", "")
        if not self.model:
            raise HostedLLMError("set HARBOR_MODEL to the model name to use")
        self.max_tokens = max_tokens
        if client is None:
            try:
                import anthropic
            except ImportError as e:
                raise HostedLLMError(
                    "the hosted model needs the anthropic package: "
                    "pip install -e '.[hosted]'") from e
            client = anthropic.Anthropic()  # reads ANTHROPIC_API_KEY from the environment
        self._client = client

    def complete(self, system: str, messages: list[dict], tools: list[dict]) -> LLMReply:
        kwargs = {"model": self.model, "max_tokens": self.max_tokens, "system": system,
                  "messages": to_anthropic(messages)}
        if tools:
            kwargs["tools"] = tools  # {name, description, input_schema} is the API's own shape
        response = self._client.messages.create(**kwargs)
        return from_anthropic(response, self.model)


def to_anthropic(messages: list[dict]) -> list[dict]:
    """Neutral messages -> Messages API messages. Consecutive tool results become
    one user message of tool_result blocks, as the API expects."""
    out: list[dict] = []
    for m in messages:
        if m["role"] == "user":
            out.append({"role": "user", "content": m["content"]})
        elif m["role"] == "assistant":
            if m.get("raw") is not None:   # replay the provider's blocks unchanged
                content = m["raw"]
            else:
                content = ([{"type": "text", "text": m["content"]}] if m.get("content") else [])
                content += [{"type": "tool_use", "id": c["id"], "name": c["name"],
                             "input": c["arguments"]} for c in m.get("tool_calls", ())]
            out.append({"role": "assistant", "content": content})
        elif m["role"] == "tool":
            block = {"type": "tool_result", "tool_use_id": m["tool_call_id"],
                     "content": m["content"]}
            if _is_error(m["content"]):
                block["is_error"] = True
            if out and out[-1]["role"] == "user" and isinstance(out[-1]["content"], list):
                out[-1]["content"].append(block)
            else:
                out.append({"role": "user", "content": [block]})
    return out


def _is_error(content: str) -> bool:
    try:
        return json.loads(content).get("ok") is False
    except (ValueError, AttributeError):
        return False


def from_anthropic(response, model: str) -> LLMReply:
    texts, calls, raw = [], [], []
    for block in response.content:
        raw.append(block.to_dict() if hasattr(block, "to_dict") else block.model_dump())
        if block.type == "text":
            texts.append(block.text)
        elif block.type == "tool_use":
            calls.append(ToolCall(block.id, block.name, dict(block.input)))
    usage = getattr(response, "usage", None)
    reply = LLMReply(
        text="".join(texts), tool_calls=calls, model=getattr(response, "model", model),
        input_tokens=getattr(usage, "input_tokens", 0) or 0,
        output_tokens=getattr(usage, "output_tokens", 0) or 0, raw=raw)
    if getattr(response, "stop_reason", None) == "refusal":
        reply.text, reply.tool_calls = "", []   # a refusal ends the agent; talk uses the fallback
    return reply
