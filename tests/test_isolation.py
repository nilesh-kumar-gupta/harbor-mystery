"""Knowledge isolation: no character can put a clue they do not know into the
notebook, whatever the model asks for. (The referee half arrives in M4.)"""

import pytest

from harbor.engine import events
from harbor.engine.case_loader import load_case
from harbor.tools import REGISTRY, Draft

from conftest import CASE_PATH

CASE = load_case(CASE_PATH)
KNOWS = {(k.character_id, k.clue_id) for k in CASE.knowledge}
UNKNOWN = [(c.id, clue.id) for c in CASE.characters for clue in CASE.clues
           if (c.id, clue.id) not in KNOWS]
ROUTE = {"quay": [], "office": ["office"], "fish_market": ["fish market"],
         "chandlery": ["chandlery"], "salt_cask": ["fish market", "salt cask"],
         "lighthouse": ["chandlery", "lighthouse"]}


def make_active(ctx, character_id, runner=None):
    """Walk to a character and talk to them, through tools only, so they are active."""
    start = next(c.start_place for c in CASE.characters if c.id == character_id)
    for place in ROUTE[start]:
        assert REGISTRY.call(ctx, "go", {"place": place}, "gm")["ok"]
    ctx.npc_runner = runner or (lambda *a: Draft("Hm."))
    return REGISTRY.call(ctx, "talk", {"character": character_id, "message": "hello"}, "gm")


@pytest.mark.parametrize("character_id, clue_id", UNKNOWN)
def test_reveal_clue_refuses_a_clue_the_character_does_not_know(ctx, character_id, clue_id):
    assert make_active(ctx, character_id)["ok"]
    before = events.state_hash(ctx.game.conn, ctx.game.run_id)
    out = REGISTRY.call(ctx, "reveal_clue", {"clue_id": clue_id}, "npc")
    assert out == {"ok": False, "error": f"you do not know clue {clue_id}"}
    assert events.state_hash(ctx.game.conn, ctx.game.run_id) == before


@pytest.mark.parametrize("character_id", [c.id for c in CASE.characters])
def test_recall_knowledge_returns_only_the_active_characters_rows(ctx, character_id):
    assert make_active(ctx, character_id)["ok"]
    out = REGISTRY.call(ctx, "recall_knowledge", {}, "npc")
    assert {c["id"] for c in out["clues"]} <= {cl for ch, cl in KNOWS if ch == character_id}
    own_pointers = {p.clue_id for p in CASE.pointers if p.character_id == character_id}
    assert {p["clue"] for p in out["pointers"]} <= own_pointers


@pytest.mark.parametrize("clue_id", ["C99", "", "c3", "C3; DROP TABLE clues"])
def test_unknown_clue_ids_are_refused(ctx, clue_id):
    assert make_active(ctx, "mira")["ok"]
    assert REGISTRY.call(ctx, "reveal_clue", {"clue_id": clue_id}, "npc") == {
        "ok": False, "error": f"unknown clue {clue_id!r}"}


# ---- M4: the referee ----------------------------------------------------------

import json  # noqa: E402

from harbor import referee  # noqa: E402
from harbor.agents.npc import make_npc_runner  # noqa: E402
from harbor.engine import state  # noqa: E402
from harbor.llm.scripted import ScriptedLLM  # noqa: E402

CHARS = {c.id: c for c in CASE.characters}


def spans_of(ctx, kind):
    return [dict(r) for r in ctx.game.conn.execute(
        "SELECT * FROM spans WHERE kind = ? ORDER BY rowid", (kind,))]


@pytest.mark.parametrize("character_id, clue_id", UNKNOWN)
def test_the_referee_rejects_every_leak_and_falls_back(ctx, character_id, clue_id):
    """A scripted NPC that states a clue it does not know is rejected three times
    (first draft and 2 regenerations); talk returns the fallback line."""
    text = next(c.text for c in CASE.clues if c.id == clue_id)
    llm = ScriptedLLM([f"Between us: {text}"] * 3)
    route_and_talk = make_active(ctx, character_id, runner=make_npc_runner(llm))
    out = route_and_talk
    assert out["ok"] and out["reply"] == CHARS[character_id].fallback_line
    assert out["fallback"] is True and out["new_clues"] == []
    assert clue_id not in state.found_clue_ids(ctx.game)
    verdicts = spans_of(ctx, "referee")
    assert [v["status"] for v in verdicts] == ["rejected"] * 3
    assert all(clue_id in json.loads(v["attrs"])["game.leaked_clues"] for v in verdicts)
    drafts = [s for s in spans_of(ctx, "agent") if s["name"] == f"npc:{character_id}"]
    assert [d["status"] for d in drafts] == ["rejected"] * 3
    assert text in json.loads(drafts[0]["output"])["text"]          # raw draft kept in its span
    said = [r["text"] for r in state.conversation(ctx.game, character_id)]
    assert all(text not in line for line in said)                   # never stored or returned
    assert "do not know" in llm.calls[1]["messages"][0]["content"]  # the regeneration note


@pytest.mark.parametrize("character", list(CHARS))
def test_fallback_lines_never_trip_the_code_check(ctx, character):
    assert referee.code_check(ctx.game, character, "", CHARS[character].fallback_line).ok


@pytest.mark.parametrize("pointer", CASE.pointers, ids=lambda p: f"{p.character_id}-{p.clue_id}")
def test_pointer_lines_never_trip_the_code_check(ctx, pointer):
    """The engine's own pointer lines must pass for the character who says them."""
    assert referee.code_check(ctx.game, pointer.character_id, "", pointer.line).ok


def test_a_revealed_clue_may_be_stated(ctx):
    llm = ScriptedLLM([{"tool_calls": [("reveal_clue", {"clue_id": "C3"})]},
                       "Someone with a limp and a lantern, near two."])
    out = make_active(ctx, "mira", runner=make_npc_runner(llm))
    assert out["new_clues"] == ["C3"] and "lantern" in out["reply"]
    assert [v["status"] for v in spans_of(ctx, "referee")] == ["ok"]


def test_a_clue_the_player_names_may_be_echoed(ctx):
    REGISTRY.call(ctx, "look", {}, "gm")                        # C1 in the notebook
    ctx.npc_runner = make_npc_runner(ScriptedLLM(["Cut clean, you say? Not by my tools."]))
    REGISTRY.call(ctx, "go", {"place": "chandlery"}, "gm")
    out = REGISTRY.call(ctx, "talk", {"character": "ysolde",
                                      "message": "The cable was cut clean."}, "gm")
    assert out["reply"].startswith("Cut clean") and not out.get("fallback")


def test_a_regenerated_reply_that_is_clean_is_accepted(ctx):
    llm = ScriptedLLM(["Brask bought bolt cutters from Ysolde.", "Ask Mira. She's up all hours."])
    ctx.npc_runner = make_npc_runner(llm)
    REGISTRY.call(ctx, "go", {"place": "office"}, "gm")
    out = REGISTRY.call(ctx, "talk", {"character": "odrin", "message": "Who did it?"}, "gm")
    assert out["reply"] == "Ask Mira. She's up all hours." and not out.get("fallback")
    assert [v["status"] for v in spans_of(ctx, "referee")] == ["rejected", "ok"]
    assert [r["text"] for r in state.conversation(ctx.game, "odrin")] == [
        "Who did it?", "Ask Mira. She's up all hours."]


def test_the_model_check_runs_only_after_the_code_check_passes(ctx):
    checker = ScriptedLLM(["LEAK: invents a boat", "OK"])
    ctx.referee_llm = checker
    ctx.npc_runner = make_npc_runner(ScriptedLLM(
        ["Bolt cutters, I heard.", "A red boat came by.", "Quiet night."]))
    out = REGISTRY.call(ctx, "talk", {"character": "mira", "message": "Anything?"}, "gm")
    assert out["reply"] == "Quiet night."
    # draft 1 failed the code check (no model call); draft 2 failed the model; draft 3 passed
    assert len(checker.calls) == 2
    content = checker.calls[0]["messages"][0]["content"]
    assert "A red boat came by." in content and "monopoly" not in content
    statuses = [(json.loads(v["attrs"])["referee.code_check"],
                 json.loads(v["attrs"])["referee.model_check"], v["status"])
                for v in spans_of(ctx, "referee")]
    assert statuses == [("leak", "skipped: code check failed", "rejected"),
                        ("pass", "leak", "rejected"), ("pass", "pass", "ok")]


def test_a_failing_check_model_does_not_block_the_reply(ctx):
    ctx.referee_llm = ScriptedLLM([])                            # raises ScriptExhausted
    ctx.npc_runner = make_npc_runner(ScriptedLLM(["Morning."]))
    out = REGISTRY.call(ctx, "talk", {"character": "mira", "message": "Hello"}, "gm")
    assert out["reply"] == "Morning."
    assert spans_of(ctx, "llm_call")[-1]["status"] == "error"


# ---- M4 review: paraphrases, side effects and false rejections ----------------

PARAPHRASES = [(c, clue.id, kw) for c, clue_id in UNKNOWN for clue in CASE.clues
               if clue.id == clue_id for kw in clue.keywords]


@pytest.mark.parametrize("character_id, clue_id, keyword", PARAPHRASES)
def test_a_paraphrase_with_any_one_keyword_is_caught(ctx, character_id, clue_id, keyword):
    """Not just verbatim clue text: one keyword in other words is a leak, unless the
    word is part of the character's own persona or speech style."""
    char = CHARS[character_id]
    verdict = referee.code_check(ctx.game, character_id, "Anything to tell me?",
                                 f"Word is, {keyword} came into it somehow.")
    if keyword.lower() in f"{char.persona} {char.speech_style}".lower():
        assert verdict.ok                         # e.g. Tom's own bad knee
    else:
        assert not verdict.ok and clue_id in verdict.leaked


def test_the_brief_example_leak_without_bolt_is_caught(ctx):
    verdict = referee.code_check(ctx.game, "odrin", "Who did it?",
                                 "Brask bought cutters from Ysolde two days back.")
    assert not verdict.ok and verdict.leaked == ["C5"]


@pytest.mark.parametrize("character_id, message, reply", [
    ("tom", "How are you?", "My knee's bad today, friend. Mackerel?"),
    ("hale", "How's business?", "These harbor fees will be the death of me."),
    ("mira", "Did you see a skiff?", "A skiff? No... ask Bram."),
    ("bram", "Who should I ask?", "Anyone cutting a cable needed cutters. Ysolde sells them."),
])
def test_ordinary_replies_are_not_rejected(ctx, character_id, message, reply):
    assert referee.code_check(ctx.game, character_id, message, reply).ok


def test_a_rejected_draft_writes_nothing(ctx):
    """Mira reveals C3 in each draft but every draft leaks C5: the player hears only the
    fallback line, so C3 must not reach the notebook."""
    drafts = []
    for _ in range(3):
        drafts += [{"tool_calls": [("reveal_clue", {"clue_id": "C3"})]},
                   "A limp and a lantern, near two. And Tom bought bolt cutters."]
    out = make_active(ctx, "mira", runner=make_npc_runner(ScriptedLLM(drafts)))
    assert out["fallback"] is True and out["new_clues"] == []
    assert "C3" not in state.found_clue_ids(ctx.game)
    assert len(spans_of(ctx, "referee")) == 3     # the spans are kept


def test_a_regenerated_draft_keeps_only_its_own_writes(ctx):
    drafts = [{"tool_calls": [("reveal_clue", {"clue_id": "C8"})]},
              "The tavern was loud till 3 a.m. and Tom bought bolt cutters.",
              {"tool_calls": [("reveal_clue", {"clue_id": "C3"})]},
              "A limp and a lantern, near two."]
    out = make_active(ctx, "mira", runner=make_npc_runner(ScriptedLLM(drafts)))
    assert out["new_clues"] == ["C3"] and "C8" not in state.found_clue_ids(ctx.game)


def test_a_paraphrase_the_keywords_miss_is_caught_by_the_model_check(ctx):
    checker = ScriptedLLM(lambda system, messages, tools:
                          "LEAK" if "no lights" in messages[0]["content"] else "OK")
    ctx.referee_llm = checker
    llm = ScriptedLLM(["A boat went out with no lights, I'd say.", "Quiet night, love."])
    out = make_active(ctx, "mira", runner=make_npc_runner(llm))
    assert out["reply"] == "Quiet night, love."
    assert [v["status"] for v in spans_of(ctx, "referee")] == ["rejected", "ok"]


def test_the_model_check_is_told_what_the_player_stated(ctx):
    REGISTRY.call(ctx, "look", {}, "gm")
    REGISTRY.call(ctx, "go", {"place": "chandlery"}, "gm")
    checker = ScriptedLLM(["OK"])
    ctx.referee_llm = checker
    ctx.npc_runner = make_npc_runner(ScriptedLLM(["Cut clean, you say."]))
    REGISTRY.call(ctx, "talk", {"character": "ysolde", "message": "The cable was cut clean."},
                  "gm")
    sent = json.loads(checker.calls[0]["messages"][0]["content"])
    assert sent["facts_the_player_stated"] == [
        "The winch cable was cut clean, and the pins were knocked loose."]


def test_the_referee_cannot_write(ctx, monkeypatch):
    def writing_check(*a, **k):
        state.advance_clock(ctx.game, 60)
    monkeypatch.setattr(referee, "check", writing_check)
    out = make_active(ctx, "mira", runner=lambda *a: Draft("Hello."))
    assert out["ok"] is False and "WriteOutsideTool" in out["error"]
    assert state.read(ctx.game)["clock"] == 360
