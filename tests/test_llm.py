import pytest

from harbor.llm.base import LLMClient, LLMReply, ToolCall
from harbor.llm.scripted import ScriptedLLM, ScriptExhausted


def test_scripted_replies_come_back_in_order():
    llm = ScriptedLLM(["hello", {"text": "", "tool_calls": [("look", {})]},
                       LLMReply(text="done", tool_calls=[ToolCall("x", "go", {"place": "quay"})])])
    assert isinstance(llm, LLMClient)
    first = llm.complete("sys", [{"role": "user", "content": "hi"}], [])
    assert (first.text, first.tool_calls, first.model) == ("hello", [], "scripted")
    second = llm.complete("sys", [], [{"name": "look"}])
    assert [(c.name, c.arguments) for c in second.tool_calls] == [("look", {})]
    assert second.tool_calls[0].id == "call_2_0"
    assert llm.complete("sys", [], []).tool_calls[0].arguments == {"place": "quay"}
    assert llm.remaining == 0
    with pytest.raises(ScriptExhausted):
        llm.complete("sys", [], [])


def test_scripted_records_what_it_was_given():
    llm = ScriptedLLM(["ok"])
    llm.complete("the system prompt", [{"role": "user", "content": "hi"}], [{"name": "look"}])
    assert llm.calls == [{"system": "the system prompt",
                          "messages": [{"role": "user", "content": "hi"}], "tools": ["look"]}]


def test_a_policy_function_decides_each_reply():
    def policy(system, messages, tools):
        return f"{len(messages)} messages"
    llm = ScriptedLLM(policy)
    assert llm.complete("s", [{}, {}], []).text == "2 messages"
    assert llm.complete("s", [{}], []).text == "1 messages"


def test_reply_as_message():
    reply = LLMReply(text="t", tool_calls=[ToolCall("id1", "look", {})])
    assert reply.as_message() == {"role": "assistant", "content": "t",
                                  "tool_calls": [{"id": "id1", "name": "look", "arguments": {}}]}
