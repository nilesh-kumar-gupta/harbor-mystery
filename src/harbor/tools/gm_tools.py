"""The game master's tools. Each validates through the engine, then changes
state through it, and returns plain JSON."""

from __future__ import annotations

from harbor import prompts, referee
from harbor.engine import hints, rules, state
from harbor.tools.registry import REGISTRY, Context, error

LOOK_MINUTES = 5
GO_MINUTES = 10
TALK_MINUTES = 10
HINT_MINUTES = 20
REFEREE_ATTEMPTS = 3  # the first draft and up to 2 regenerations


def _names(rows) -> list[str]:
    return [r["name"] for r in rows]


@REGISTRY.tool("look", "gm",
               "Look around the current place: description, visible clues, who is here, "
               "exits and the clock. Visible clues go into the notebook. Costs 5 minutes.")
def look(ctx: Context) -> dict:
    game = ctx.game
    st = state.read(game)
    here = rules.place(game, st["place"])
    clues = rules.visible_clues(game, here["id"])
    clock = state.advance_clock(game, LOOK_MINUTES)  # first, so finds are stamped with the
    for clue in clues:                               # time the action ends (ADR 002 #8)
        state.add_clue(game, clue["id"], None)
    return {
        "ok": True, "place": here["name"], "description": here["description"],
        "present": _names(rules.present(game, here["id"])),
        "clues": [{"id": c["id"], "text": c["text"]} for c in clues],
        "exits": _names(rules.exits(game, here["id"])),
        "clock": state.clock_text(clock),
    }


@REGISTRY.tool("go", "gm",
               "Move the player to a neighbouring place. Costs 10 minutes.",
               {"place": {"type": "string", "description": "Name or id of the place"}},
               required=("place",))
def go(ctx: Context, place: str) -> dict:
    game = ctx.game
    st = state.read(game)
    here = rules.place(game, st["place"])
    exits = rules.exits(game, here["id"])
    target = rules.resolve_place(game, place)
    if target is None:
        return error(f"unknown place {place!r}", exits=_names(exits))
    if target["id"] == here["id"]:
        return error(f"already at {here['name']}", exits=_names(exits))
    if target["id"] not in [e["id"] for e in exits]:
        return error(f"no exit from {here['name']} to {target['name']}", exits=_names(exits))
    state.move(game, target["id"])
    clock = state.advance_clock(game, GO_MINUTES)
    return {"ok": True, "place": target["name"],
            "present": _names(rules.present(game, target["id"])),
            "clock": state.clock_text(clock)}


@REGISTRY.tool("notebook", "gm", "List the clues found so far, with source and place. Free.")
def notebook(ctx: Context) -> dict:
    return {"ok": True, "clues": [
        {"id": r["clue_id"], "text": r["text"], "source": r["source_name"] or "seen",
         "place": r["place_name"], "at": state.clock_text(r["minute"])}
        for r in state.notebook(ctx.game)]}


def state_summary(ctx: Context) -> dict:
    """The compact state the game master sees each turn. Read-only; not a tool."""
    game = ctx.game
    st = state.read(game)
    return {"place": rules.place(game, st["place"])["name"],
            "clock": state.clock_text(st["clock"]),
            "notebook": [f"{r['clue_id']}: {r['text']}" for r in state.notebook(game)],
            "status": st["status"]}


@REGISTRY.tool("talk", "gm",
               "Say something to a character who is in the same place. Returns their reply "
               "and the ids of any clues they revealed. Costs 10 minutes.",
               {"character": {"type": "string", "description": "Name or id of the character"},
                "message": {"type": "string", "description": "What the player says"}},
               required=("character", "message"))
def talk(ctx: Context, character: str, message: str) -> dict:
    game = ctx.game
    st = state.read(game)
    here = _names(rules.present(game, st["place"]))
    who = rules.resolve_character(game, character)
    if who is None:
        return error(f"unknown character {character!r}", present=here)
    if rules.character_place(game, who["id"]) != st["place"]:
        return error(f"{who['name']} is not here", present=here)
    if not message.strip():
        return error("message is empty")
    if ctx.npc_runner is None:
        return error("no NPC agent is configured")

    before = state.found_clue_ids(game)
    clock = state.advance_clock(game, TALK_MINUTES)  # first: finds carry the end time
    state.start_talk(game, who["id"])          # the active-character rule: set here only
    state.say(game, who["id"], "player", message)
    shown = sorted(rules.shown_clues(game, message))
    if shown:
        state.record_shown(game, who["id"], shown)
    reply, fallback = _npc_reply(ctx, who, message)
    state.say(game, who["id"], "npc", reply)
    out = {"ok": True, "character": who["name"], "reply": reply,
           "new_clues": [r["clue_id"] for r in state.notebook(game)
                         if r["clue_id"] not in before],
           "clock": state.clock_text(clock)}
    if fallback:
        out["fallback"] = True
    return out


def _npc_reply(ctx: Context, who, message: str) -> tuple[str, bool]:
    """Draft, referee, regenerate. Returns (accepted reply, used the fallback line).

    Every draft is an agent span; a draft the referee rejects keeps its raw text
    in that span with status `rejected`, and is never stored or returned.
    """
    card = {key: who[key] for key in
            ("id", "name", "role", "persona", "speech_style", "fallback_line")}
    note = None
    conn = ctx.game.conn
    for attempt in range(1, REFEREE_ATTEMPTS + 1):
        # an attempt is all or nothing: what a rejected draft's tool calls wrote (a
        # reveal, a trust step) is rolled back, since the player never hears it
        savepoint = f"npc_attempt_{attempt}"
        conn.execute(f"SAVEPOINT {savepoint}")
        draft, span = _draft(ctx, card, message, note, attempt)
        verdict = None
        if draft is not None:
            depth, ctx.game.tool_depth = ctx.game.tool_depth, 0   # the referee only reads
            try:
                verdict = referee.check(ctx, card, message, draft, ctx.referee_llm,
                                        prompts.load("referee"), attempt)
            finally:
                ctx.game.tool_depth = depth
        if verdict is not None and verdict.ok:
            conn.execute(f"RELEASE {savepoint}")
            return draft, False
        conn.execute(f"ROLLBACK TO {savepoint}")
        conn.execute(f"RELEASE {savepoint}")
        if draft is None:          # the agent failed, hit its cap or said nothing
            return who["fallback_line"], True
        ctx.tracer.amend(span, status="rejected")
        note = referee.LEAK_NOTE
    return who["fallback_line"], True


def _draft(ctx: Context, card: dict, message: str, note: str | None, attempt: int):
    attrs = {"gen_ai.operation.name": "invoke_agent", "gen_ai.agent.name": f"npc:{card['id']}",
             "game.npc": card["id"], "game.turn": state.read(ctx.game)["turn"],
             "game.attempt": attempt}
    with ctx.tracer.span("agent", f"npc:{card['id']}", input=message, attrs=attrs) as span:
        depth, ctx.game.tool_depth = ctx.game.tool_depth, 0   # agent code may not write;
        try:                                                 # only its own tool calls can
            draft = ctx.npc_runner(ctx, card, message, note)
        except Exception as e:  # a failing model must not leave the conversation half-written
            span.status = "error"
            span.attrs["error.type"] = type(e).__name__
            span.output = {"error": str(e)}
            return None, span
        finally:
            ctx.game.tool_depth = depth
        span.output = {"text": draft.text, "capped": draft.capped}
        if draft.capped or not draft.text.strip():
            span.attrs["game.fallback"] = "tool_limit" if draft.capped else "empty_reply"
            return None, span
        return draft.text.strip(), span


@REGISTRY.tool("hint", "gm",
               "Ask for a hint: the engine names a person and place worth a visit. "
               "Costs 20 minutes.")
def hint(ctx: Context) -> dict:
    game = ctx.game
    target = hints.hint_target(game)
    clock = state.advance_clock(game, HINT_MINUTES)
    state.note_hint(game, target["clue_id"] if target else None)
    shown = {k: target[k] for k in ("character", "place")} if target else None
    return {"ok": True, "target": shown, "clock": state.clock_text(clock)}
