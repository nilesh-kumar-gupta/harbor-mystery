"""M3: the agent loop, the NPC agent, `talk` with the active-character rule and
per-character trust, and the game master, all through ScriptedLLM."""

import json

import pytest

from harbor.agents.game_master import CAP_LINE, GameMaster
from harbor.agents.loop import run_loop
from harbor.agents.npc import make_npc_runner
from harbor.engine import events, state
from harbor.llm.scripted import ScriptedLLM
from harbor.offline import npc_policy
from harbor.tools import REGISTRY, Context, Draft


def snapshot(ctx):
    g = ctx.game
    return events.state_hash(g.conn, g.run_id), len(events.log(g.conn, g.run_id))


def walk(ctx, *places):
    for place in places:
        assert REGISTRY.call(ctx, "go", {"place": place}, "gm")["ok"], place


def talk(ctx, who, message):
    return REGISTRY.call(ctx, "talk", {"character": who, "message": message}, "gm")


def npc(ctx, *replies):
    """Attach a scripted NPC agent to the context; returns its ScriptedLLM."""
    llm = ScriptedLLM(list(replies))
    ctx.npc_runner = make_npc_runner(llm)
    return llm


@pytest.fixture
def offline_npc(ctx):
    ctx.npc_runner = make_npc_runner(ScriptedLLM(npc_policy))
    return ctx


# ---- talk: validation ----------------------------------------------------

@pytest.mark.parametrize("who, message, error", [
    ("tom", "hello", "Tom Brask is not here"),
    ("the mayor", "hello", "unknown character 'the mayor'"),
    ("mira", "   ", "message is empty"),
])
def test_bad_talk_is_an_error_value_and_changes_nothing(offline_npc, who, message, error):
    ctx = offline_npc
    before = snapshot(ctx)
    out = talk(ctx, who, message)
    assert out["ok"] is False and out["error"] == error
    assert snapshot(ctx) == before and state.read(ctx.game)["active_character"] is None


def test_talk_without_an_npc_agent_is_an_error(ctx):
    assert talk(ctx, "mira", "hello") == {"ok": False, "error": "no NPC agent is configured"}


# ---- STRUCTURE example 3: a clue unlocked by topic --------------------------

def test_mira_reveals_c3_when_asked_about_the_night(offline_npc):
    ctx = offline_npc
    out = talk(ctx, "mira", "Did you see anyone on the quay last night?")
    assert out["ok"] and out["character"] == "Mira Tolle" and out["new_clues"] == ["C3"]
    assert "limp and a lantern" in out["reply"] and "Ask Bram" in out["reply"]
    assert out["clock"] == "06:10"
    st = state.read(ctx.game)
    assert st["active_character"] == "mira"
    row = [r for r in state.notebook(ctx.game) if r["clue_id"] == "C3"][0]
    assert row["source_character"] == "mira"
    lines = [(r["speaker"], r["text"]) for r in state.conversation(ctx.game, "mira")]
    assert lines == [("player", "Did you see anyone on the quay last night?"),
                     ("npc", out["reply"])]


def test_an_off_topic_question_unlocks_nothing(offline_npc):
    out = talk(offline_npc, "mira", "Lovely weather for mending.")
    assert out["ok"] and out["new_clues"] == []
    assert "Ask Bram" in out["reply"]           # the pointer is still offered


def test_saying_a_clue_is_not_revealing_it(ctx):
    npc(ctx, "A limping man with a lantern, near two.")       # no reveal_clue call
    out = talk(ctx, "mira", "Anything last night?")
    assert out["ok"] and out["new_clues"] == [] and "C3" not in state.found_clue_ids(ctx.game)


# ---- STRUCTURE example 4: a guarded clue and trust ---------------------------

def test_ysolde_guards_c5_until_asked_politely_twice(ctx):
    walk(ctx, "chandlery")
    llm = npc(ctx, {"tool_calls": [("recall_knowledge", {})]},
              {"tool_calls": [("reveal_clue", {"clue_id": "C5"})]},
              "I don't discuss my customers' purchases.")
    out = talk(ctx, "ysolde", "Who has bought bolt cutters from you lately?")
    results = [json.loads(m["content"]) for m in llm.calls[-1]["messages"] if m["role"] == "tool"]
    assert results[0] == {"ok": True, "clues": [], "pointers": [], "guarded": True, "trust": 0,
                          "shown": []}
    assert results[1] == {"ok": False, "error": "trust too low"}
    assert out["new_clues"] == [] and "C5" not in state.found_clue_ids(ctx.game)

    npc(ctx, {"tool_calls": [("update_trust", {"reason": "polite_repeat"})]},
        {"tool_calls": [("recall_knowledge", {})]},
        {"tool_calls": [("reveal_clue", {"clue_id": "C5"})]},
        "Tom Brask. Two days ago. He paid in fish.")
    out = talk(ctx, "ysolde", "I understand. Please, it matters for the whole harbor.")
    assert out["new_clues"] == ["C5"] and state.trust_of(ctx.game, "ysolde") == 1


def test_polite_repeat_needs_an_earlier_ask(ctx):
    walk(ctx, "chandlery")
    llm = npc(ctx, {"tool_calls": [("update_trust", {"reason": "polite_repeat"})]}, "Hm.")
    talk(ctx, "ysolde", "Please, who bought cutters?")
    result = json.loads(llm.calls[-1]["messages"][-1]["content"])
    assert result == {"ok": False, "error": "the player has not asked before", "trust": 0}
    assert state.trust_of(ctx.game, "ysolde") == 0


def test_showing_a_related_clue_raises_trust_once(ctx):
    REGISTRY.call(ctx, "look", {}, "gm")                     # C1 into the notebook
    walk(ctx, "chandlery")
    npc(ctx, {"tool_calls": [("update_trust", {"reason": "related_clue_shown"})]},
        {"tool_calls": [("reveal_clue", {"clue_id": "C5"})]}, "Tom Brask bought them.")
    out = talk(ctx, "ysolde", "The cable was cut clean. Who buys tools that do that?")
    assert out["new_clues"] == ["C5"] and state.trust_of(ctx.game, "ysolde") == 1
    llm = npc(ctx, {"tool_calls": [("update_trust", {"reason": "related_clue_shown"})]}, "Yes.")
    talk(ctx, "ysolde", "Remember, it was cut clean.")
    assert json.loads(llm.calls[-1]["messages"][-1]["content"])["ok"] is False
    assert state.trust_of(ctx.game, "ysolde") == 1           # the same clue counts once


def test_a_clue_not_in_the_notebook_cannot_be_shown(ctx):
    walk(ctx, "chandlery")                                    # C1 never looked at
    llm = npc(ctx, {"tool_calls": [("update_trust", {"reason": "related_clue_shown"})]}, "No.")
    talk(ctx, "ysolde", "The cable was cut clean!")
    assert json.loads(llm.calls[-1]["messages"][-1]["content"])["ok"] is False


def test_rude_lowers_trust_and_trust_is_per_character(ctx):
    REGISTRY.call(ctx, "look", {}, "gm")
    walk(ctx, "chandlery")
    npc(ctx, {"tool_calls": [("update_trust", {"reason": "related_clue_shown"})]}, "Fine.")
    talk(ctx, "ysolde", "It was cut clean, look.")
    npc(ctx, {"tool_calls": [("update_trust", {"reason": "rude"})]}, "Out.")
    talk(ctx, "ysolde", "Hurry up, you useless shopkeeper.")
    assert state.trust_of(ctx.game, "ysolde") == 0
    assert {r["character_id"]: r["level"] for r in ctx.game.conn.execute(
        "SELECT * FROM trust")}["mira"] == 0


def test_trust_moves_at_most_one_step_per_conversation(ctx):
    REGISTRY.call(ctx, "look", {}, "gm")
    walk(ctx, "chandlery")
    npc(ctx, "Good day.")
    talk(ctx, "ysolde", "Good day.")                       # an earlier ask exists
    llm = npc(ctx, {"tool_calls": [("update_trust", {"reason": "related_clue_shown"})]},
              {"tool_calls": [("update_trust", {"reason": "polite_repeat"})]}, "Hm.")
    talk(ctx, "ysolde", "Please. It was cut clean.")       # both reasons would qualify
    talk_results = [json.loads(m["content"]) for m in llm.calls[-1]["messages"]
                    if m["role"] == "tool"]
    assert talk_results[0]["ok"]
    assert talk_results[1] == {"ok": False, "trust": 1,
                               "error": "trust already changed in this conversation"}
    assert state.trust_of(ctx.game, "ysolde") == 1


def test_polite_repeat_must_be_polite(ctx):
    walk(ctx, "chandlery")
    npc(ctx, "Hm.")
    talk(ctx, "ysolde", "Who bought cutters?")
    llm = npc(ctx, {"tool_calls": [("update_trust", {"reason": "polite_repeat"})]}, "Hm.")
    talk(ctx, "ysolde", "Hurry up and tell me.")
    assert json.loads(llm.calls[-1]["messages"][-1]["content"])["error"] == \
        "the player did not ask politely"


def test_clues_named_before_they_were_found_do_not_count_later(offline_npc):
    ctx = offline_npc
    walk(ctx, "fish market")
    talk(ctx, "tom", "Carrying a lantern last night? Bought any bolt cutters?")  # nothing found yet
    walk(ctx, "quay")
    talk(ctx, "mira", "Anyone on the quay last night?")                          # C3
    REGISTRY.call(ctx, "look", {}, "gm")
    walk(ctx, "chandlery")
    talk(ctx, "ysolde", "The cable was cut clean. Who bought cutters?")         # C5
    walk(ctx, "quay", "fish market")
    out = talk(ctx, "tom", "Nice fish.")
    assert "C9" not in out["new_clues"]


def test_the_last_talk_of_the_day_still_works(offline_npc):
    ctx = offline_npc
    for _ in range(142):                                     # 142 x 5 minutes
        REGISTRY.call(ctx, "look", {}, "gm")
    assert REGISTRY.call(ctx, "look", {}, "gm")["clock"] == "17:55"
    out = talk(ctx, "mira", "Did you see anyone on the quay last night?")
    assert out["ok"] and out["new_clues"] == ["C3"] and not out.get("fallback")


def test_tom_confesses_when_shown_c3_and_c5(offline_npc, ctx):
    talk(ctx, "mira", "Who was on the quay last night?")            # C3
    REGISTRY.call(ctx, "look", {}, "gm")                              # C1
    walk(ctx, "chandlery")
    talk(ctx, "ysolde", "The cable was cut clean. Who bought cutters?")  # trust 1 -> C5
    assert {"C1", "C3", "C5"} <= state.found_clue_ids(ctx.game)
    walk(ctx, "quay", "fish market")
    out = talk(ctx, "tom", "Someone with a limp and a lantern, and you bought bolt cutters.")
    assert "C9" in out["new_clues"]


def test_hale_admits_c7_only_when_confronted(offline_npc, ctx):
    recall = lambda: [c["id"] for c in REGISTRY.call(ctx, "recall_knowledge", {}, "npc")["clues"]]
    walk(ctx, "fish market", "salt cask")
    talk(ctx, "hale", "What do you know about the harbor fees?")
    assert "C7" not in recall()                       # min_trust 2, and not yet confronted
    walk(ctx, "fish market", "quay", "office")
    talk(ctx, "odrin", "Any trouble with the harbor fees?")
    assert "C7" in state.found_clue_ids(ctx.game)     # Odrin tells it
    walk(ctx, "quay", "fish market", "salt cask")
    talk(ctx, "hale", "Odrin says you owe unpaid fees and would see the harbor sink.")
    assert "C7" in recall()                           # shown C7: unlocked at any trust
    assert state.trust_of(ctx.game, "hale") == 0


# ---- the active-character rule ---------------------------------------------

def test_npc_tools_need_an_active_character(ctx):
    for name, arguments in (("recall_knowledge", {}), ("recall_conversation", {}),
                            ("reveal_clue", {"clue_id": "C3"}),
                            ("update_trust", {"reason": "rude"})):
        assert REGISTRY.call(ctx, name, arguments, "npc") == {
            "ok": False, "error": "no conversation is in progress"}


def test_npc_tools_refuse_a_character_argument(offline_npc):
    talk(offline_npc, "mira", "hello")
    out = REGISTRY.call(offline_npc, "recall_knowledge", {"character": "tom"}, "npc")
    assert out == {"ok": False, "error": "unexpected argument 'character'"}


def test_moving_clears_the_active_character(offline_npc):
    talk(offline_npc, "mira", "hello")
    assert state.read(offline_npc.game)["active_character"] == "mira"
    walk(offline_npc, "office")
    assert state.read(offline_npc.game)["active_character"] is None


def test_recall_conversation_returns_only_this_characters_lines(offline_npc):
    ctx = offline_npc
    talk(ctx, "mira", "First question about the night.")
    walk(ctx, "office")
    talk(ctx, "odrin", "Tell me about the meeting.")
    out = REGISTRY.call(ctx, "recall_conversation", {}, "npc")
    assert [l["text"] for l in out["lines"] if l["speaker"] == "player"] == [
        "Tell me about the meeting."]


def test_the_npc_agent_cannot_write_state_except_through_its_tools(ctx):
    def sneaky(ctx_, card, message, note):
        state.advance_clock(ctx_.game, 60)            # not a tool call: the guard is closed
        return Draft("hello")
    ctx.npc_runner = sneaky
    out = talk(ctx, "mira", "hello")
    assert out["ok"] and out["reply"] == "Oh, I... no, I've lost the thread of it. These nets won't mend themselves."
    assert out["fallback"] is True and state.read(ctx.game)["clock"] == 360 + 10


def test_the_npc_sees_only_its_card_and_the_message(ctx):
    llm = npc(ctx, "Morning.")
    talk(ctx, "mira", "Good morning")
    call = llm.calls[0]
    assert "Mira Tolle" in call["system"] and "net-mender" in call["system"]
    for other in ("Tom", "Brask", "bolt", "monopoly", "Odrin", "C3"):
        assert other not in call["system"] + json.dumps(call["messages"])
    assert call["tools"] == ["recall_knowledge", "recall_conversation", "reveal_clue",
                             "update_trust"]


# ---- caps ------------------------------------------------------------------

def test_a_fifth_npc_tool_call_ends_with_the_fallback_line(ctx):
    npc(ctx, *[{"tool_calls": [("recall_conversation", {})]}] * 5)
    out = talk(ctx, "mira", "hello")
    assert out["reply"].startswith("Oh, I... no") and out["fallback"]
    assert ctx.game.conn.execute(
        "SELECT COUNT(*) FROM spans WHERE name = 'recall_conversation'").fetchone()[0] == 4


def test_an_empty_or_failing_npc_reply_uses_the_fallback_line(ctx):
    npc(ctx, "")
    assert talk(ctx, "mira", "hello")["fallback"] is True
    npc(ctx)                                          # ScriptExhausted inside the runner
    out = talk(ctx, "mira", "hello again")
    assert out["ok"] and out["fallback"] is True


def test_run_loop_counts_calls_across_one_reply(ctx):
    llm = ScriptedLLM([{"tool_calls": [("look", {}), ("look", {}), ("look", {}), ("look", {})]}])
    out = run_loop(llm, ctx.tracer, "gm", "sys", [{"role": "user", "content": "x"}], [],
                   lambda n, a: REGISTRY.call(ctx, n, a, "gm"), 3)
    assert out.capped and out.tool_results == []        # none run when the batch exceeds the cap


# ---- the game master ---------------------------------------------------------

def test_game_master_turn_runs_tools_then_end_turn(ctx):
    llm = ScriptedLLM([{"tool_calls": [("look", {})]}, "You look around the quay."])
    result = GameMaster(llm).run_turn(ctx, "look around")
    assert result.narration == "You look around the quay." and result.status == "active"
    assert [n for n, _, _ in result.tool_results] == ["look"]
    assert state.read(ctx.game)["turn"] == 2
    first = llm.calls[0]
    assert first["messages"][0]["content"].startswith("Player: look around\n\nState: ")
    assert '"place": "Harbor Quay"' in first["messages"][0]["content"]
    assert first["tools"] == ["look", "go", "notebook", "talk", "hint", "accuse"]


def test_game_master_recovers_from_an_error_value(ctx):
    llm = ScriptedLLM([{"tool_calls": [("go", {"place": "lighthouse"})]},
                       {"tool_calls": [("go", {"place": "chandlery"})]}, "To the chandlery."])
    result = GameMaster(llm).run_turn(ctx, "go to the lighthouse")
    assert [r["ok"] for _, _, r in result.tool_results] == [False, True]
    assert "no exit from Harbor Quay to Lighthouse" in llm.calls[1]["messages"][-1]["content"]


def test_game_master_cap_is_three_tool_calls(ctx):
    llm = ScriptedLLM([{"tool_calls": [("notebook", {})]}] * 4)
    result = GameMaster(llm).run_turn(ctx, "do everything")
    assert result.narration.startswith(CAP_LINE) and len(result.tool_results) == 3
    assert "Your notebook is empty." in result.narration
    assert state.read(ctx.game)["turn"] == 2            # end_turn still ran


def test_a_failing_game_master_model_still_ends_the_turn(ctx):
    result = GameMaster(ScriptedLLM([])).run_turn(ctx, "look")
    assert result.narration.startswith("(The game master could not answer.)")
    assert state.read(ctx.game)["turn"] == 2
    row = ctx.game.conn.execute("SELECT status FROM spans WHERE name = 'game_master'").fetchone()
    assert row["status"] == "error"


def test_case_solution_never_reaches_an_agent(make_session):
    session = make_session()
    for line in ("look", "talk mira: anything last night?", "go fish market",
                 "talk tom: where were you last night?"):
        session.turn(line)
    rows = session.ctx.game.conn.execute(
        "SELECT input, output FROM spans WHERE kind IN ('llm_call', 'agent')").fetchall()
    text = " ".join(f"{r['input']} {r['output']}" for r in rows)
    for secret in ("monopoly selling to the small boats", "01:45", "bolt cutters bought from"):
        assert secret not in text


def test_prompt_hash_and_model_are_recorded(make_session):
    run = make_session().ctx.game.conn.execute("SELECT * FROM runs").fetchone()
    assert len(run["prompt_hash"]) == 16 and run["model"] == "offline-gm"


def test_prompt_files_are_stubs_naming_their_tools():
    from harbor import prompts
    for name, tools in (("game_master", ["look", "go", "talk", "notebook", "hint", "accuse"]),
                        ("npc", ["recall_knowledge", "recall_conversation", "reveal_clue",
                                 "update_trust"]),
                        ("referee", [])):
        text = prompts.load(name)
        assert text.startswith("<!-- STUB: owner to rewrite -->")
        assert all(t in text for t in tools)
