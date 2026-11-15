"""HostedLLM's message conversion, checked offline with a fake client.

The real API is never called here; tests/test_live_smoke.py does that under -m live.
"""

import json
from types import SimpleNamespace as NS

import pytest

from harbor.llm.base import LLMReply, ToolCall
from harbor.llm.hosted import HostedLLM, HostedLLMError, from_anthropic, to_anthropic


class Block(NS):
    def to_dict(self):
        return dict(vars(self))


class FakeClient:
    def __init__(self, response):
        self.response, self.sent = response, None
        self.messages = self

    def create(self, **kwargs):
        self.sent = kwargs
        return self.response


def test_neutral_messages_become_api_messages():
    messages = [
        {"role": "user", "content": "hi"},
        LLMReply(text="Let me look.", tool_calls=[ToolCall("t1", "look", {}),
                                                  ToolCall("t2", "notebook", {})]).as_message(),
        {"role": "tool", "tool_call_id": "t1", "name": "look", "content": json.dumps({"ok": True})},
        {"role": "tool", "tool_call_id": "t2", "name": "notebook",
         "content": json.dumps({"ok": False, "error": "x"})},
    ]
    assert to_anthropic(messages) == [
        {"role": "user", "content": "hi"},
        {"role": "assistant", "content": [
            {"type": "text", "text": "Let me look."},
            {"type": "tool_use", "id": "t1", "name": "look", "input": {}},
            {"type": "tool_use", "id": "t2", "name": "notebook", "input": {}}]},
        {"role": "user", "content": [   # all results of one reply in one user message
            {"type": "tool_result", "tool_use_id": "t1", "content": '{"ok": true}'},
            {"type": "tool_result", "tool_use_id": "t2", "is_error": True,
             "content": '{"ok": false, "error": "x"}'}]},
    ]


def test_provider_blocks_are_replayed_unchanged():
    raw = [{"type": "thinking", "thinking": "", "signature": "sig"},
           {"type": "tool_use", "id": "t1", "name": "look", "input": {}}]
    reply = LLMReply(tool_calls=[ToolCall("t1", "look", {})], raw=raw)
    assert to_anthropic([reply.as_message()])[0]["content"] == raw


def test_a_response_becomes_a_reply_with_usage():
    response = NS(model="m", stop_reason="tool_use", usage=NS(input_tokens=11, output_tokens=7),
                  content=[Block(type="text", text="Looking."),
                           Block(type="tool_use", id="t1", name="go", input={"place": "quay"})])
    reply = from_anthropic(response, "m")
    assert reply.text == "Looking." and reply.tool_calls == [ToolCall("t1", "go", {"place": "quay"})]
    assert (reply.input_tokens, reply.output_tokens) == (11, 7) and len(reply.raw) == 2


@pytest.mark.parametrize("stop", ["refusal", "max_tokens"])
def test_a_refusal_or_cut_off_reply_ends_the_agent_with_nothing(stop):
    response = NS(model="m", stop_reason=stop, usage=None,
                  content=[Block(type="text", text="partial"),
                           Block(type="tool_use", id="t", name="go", input={"pla": 1})])
    reply = from_anthropic(response, "m")
    assert reply.text == "" and reply.tool_calls == []


def test_complete_sends_model_system_tools(monkeypatch):
    monkeypatch.setenv("HARBOR_MODEL", "some-model")
    client = FakeClient(NS(model="some-model", stop_reason="end_turn", usage=None,
                           content=[Block(type="text", text="ok")]))
    llm = HostedLLM(client=client)
    tools = [{"name": "look", "description": "d", "input_schema": {"type": "object"}}]
    assert llm.complete("sys", [{"role": "user", "content": "hi"}], tools).text == "ok"
    assert client.sent["model"] == "some-model" and client.sent["system"] == "sys"
    assert client.sent["tools"] == tools and client.sent["max_tokens"] > 0


def test_model_name_is_required(monkeypatch):
    monkeypatch.delenv("HARBOR_MODEL", raising=False)
    with pytest.raises(HostedLLMError, match="HARBOR_MODEL"):
        HostedLLM(client=object())


def test_anthropic_is_imported_lazily():
    import harbor.llm.hosted as hosted
    source = open(hosted.__file__).read()
    assert "import anthropic" not in source.split("def __init__")[0]   # not at module level
