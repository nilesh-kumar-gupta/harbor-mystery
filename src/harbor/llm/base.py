"""The model interface. It knows nothing about the game.

Messages are plain dicts in one neutral shape:
    {"role": "user", "content": "text"}
    {"role": "assistant", "content": "text", "tool_calls": [{"id", "name", "arguments"}]}
        (plus an optional "raw": the provider's own content, replayed as-is by that provider)
    {"role": "tool", "tool_call_id": "...", "name": "...", "content": "<JSON text>"}
Tool definitions are {"name", "description", "input_schema"}.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any


@dataclass
class ToolCall:
    id: str
    name: str
    arguments: dict


@dataclass
class LLMReply:
    text: str = ""
    tool_calls: list[ToolCall] = field(default_factory=list)
    model: str = ""
    input_tokens: int = 0
    output_tokens: int = 0
    raw: Any = None  # the provider's own response, if any

    def as_message(self) -> dict:
        """This reply as an assistant message to append to the conversation."""
        message = {"role": "assistant", "content": self.text,
                   "tool_calls": [{"id": c.id, "name": c.name, "arguments": c.arguments}
                                  for c in self.tool_calls]}
        if self.raw is not None:
            message["raw"] = self.raw  # lets the same provider replay its own blocks unchanged
        return message


class LLMClient(ABC):
    """One call: a system prompt, the messages so far and the tools on offer."""

    model: str = ""

    @abstractmethod
    def complete(self, system: str, messages: list[dict], tools: list[dict]) -> LLMReply:
        ...
