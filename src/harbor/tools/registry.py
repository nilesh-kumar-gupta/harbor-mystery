"""The tool registry: name -> function + JSON schema, and the one way to call a tool.

`Registry.call` is the only door into the engine. It checks the caller may use
the tool, checks the arguments against the schema, opens a tool_call span and
the write guard, and turns any exception into an error value (rolling back what
the call wrote), so an agent never sees a traceback and an error always means
nothing changed.
"""

from __future__ import annotations

import itertools
from dataclasses import dataclass, field
from typing import Any, Callable

from harbor.engine import interaction, rules, state
from harbor.engine.state import Game
from harbor.tracing import Tracer

AUDIENCES = ("gm", "npc", "referee", "engine")  # engine steps are never offered to a model
_JSON_TYPES = {"string": str, "array": list, "integer": int, "object": dict}


@dataclass
class Context:
    """Run collaborators and process-local call limits; no agent callbacks."""

    game: Game
    tracer: Tracer | None = None
    counts: dict = field(default_factory=dict)
    limited: set = field(default_factory=set)
    turn_open: bool = False
    trace_warning_seen: int = 0

    def __post_init__(self) -> None:
        # one tracer per run, so span ids never collide between contexts
        if self.tracer is None:
            self.tracer = self.game.tracer or Tracer(self.game.conn, self.game.run_id)
        self.game.tracer = self.tracer

    def begin_turn(self) -> int:
        """Start the trace for the current turn. Returns the turn number."""
        turn = state.read(self.game)["turn"]
        self.tracer.begin_trace(f"t{turn:04d}")
        return turn


@dataclass(frozen=True)
class Tool:
    name: str
    audience: str
    description: str
    input_schema: dict
    func: Callable[..., dict]
    when_ended: bool = False  # may run after the game is over


def error(message: str, **extra: Any) -> dict:
    return {"ok": False, "error": message, **extra}


class Registry:
    def __init__(self) -> None:
        self._tools: dict[str, Tool] = {}

    def tool(self, name: str, audience: str, description: str,
             properties: dict | None = None, required: tuple[str, ...] = (),
             when_ended: bool = False):
        """Decorator: register `func(ctx, **arguments) -> dict` under `name`."""
        assert audience in AUDIENCES
        schema = {"type": "object", "properties": properties or {},
                  "required": list(required), "additionalProperties": False}

        def register(func):
            self._tools[name] = Tool(name, audience, description, schema, func, when_ended)
            return func
        return register

    def get(self, name: str) -> Tool | None:
        return self._tools.get(name)

    def names(self, audience: str) -> list[str]:
        return [t.name for t in self._tools.values() if t.audience == audience]

    def schemas(self, audience: str) -> list[dict]:
        """Tool definitions to hand to a model: name, description, input_schema."""
        return [{"name": t.name, "description": t.description, "input_schema": t.input_schema}
                for t in self._tools.values() if t.audience == audience]

    def call(self, ctx: Context, name: str, arguments: dict | None, audience: str) -> dict:
        """Run one tool for a caller of the given audience, inside a tool_call span.

        Never raises: every failure comes back as {"ok": false, "error": ...}. A call
        is all or nothing: if the tool fails or returns an error value, whatever it
        wrote is rolled back, so an error always means "nothing changed". Spans are
        not lost by a rollback: the tracer writes them once the root span closes.
        """
        game = ctx.game
        try:
            st = state.read(game)
        except Exception:  # an unreadable database is still reported as a value below
            st = None
        previous = None
        valid_turn = (name == "player_turn" and audience == "gm" and st
                      and not state.is_over(game)
                      and _check_arguments(self.get("player_turn").input_schema, arguments) is None)
        if valid_turn and ctx.turn_open:
            previous = self.call(ctx, "close_turn", {}, "gm")
            if not previous.get("ok"):
                return previous
            st = state.read(game)
        if valid_turn and not ctx.turn_open:
            ctx.begin_turn()
        attrs = {"gen_ai.operation.name": "execute_tool", "gen_ai.tool.name": str(name),
                 "game.turn": st["turn"] if st else 0, "game.audience": audience}
        parent_id = None
        args = arguments if isinstance(arguments, dict) else {}
        iid, attempt = args.get("interaction_id"), args.get("attempt")
        if iid and attempt:
            attrs.update({"game.interaction_id": iid, "game.attempt": attempt})
            parent_id = f"{iid}-a{attempt}-{'npc' if audience == 'npc' else 'ref'}"
        elif ctx.turn_open or name == "player_turn":
            parent_id = f"{ctx.tracer.trace_id}-gm"
        with ctx.tracer.span("tool_call", str(name), input=arguments, attrs=attrs,
                             parent_id=parent_id) as span:
            savepoint = f"tool_{next(_savepoints)}"
            game.conn.execute(f"SAVEPOINT {savepoint}")
            outer_span, game.span_id = game.span_id, span.span_id
            game.tool_depth += 1
            result = None
            try:
                result = self._run(ctx, name, arguments, audience)
            except Exception as e:  # errors are values: the model must be able to recover
                result = error(f"internal error in {name}: {type(e).__name__}: {e}")
            finally:
                game.tool_depth -= 1
                game.span_id = outer_span
                if result is None or not result.get("ok"):  # None: interrupted, re-raising
                    game.conn.execute(f"ROLLBACK TO {savepoint}")
                game.conn.execute(f"RELEASE {savepoint}")
            if name == "close_turn" and result.get("ok"):
                sid = f"{ctx.tracer.trace_id}-gm"
                if sid in ctx.tracer._virtual:
                    gm = ctx.tracer._virtual[sid]
                    ctx.tracer.close_virtual(sid, status=gm.status, output=gm.output)
            if name == "player_turn" and previous:
                result["previous"] = previous
            if name == "begin_talk" and result.get("ok"):
                span.attrs.update({"game.interaction_id": result["interaction_id"], "game.attempt": 1})
            span.output = result
            span.status = "ok" if result.get("ok") else "error"
            if ctx.tracer.trace_failures > ctx.trace_warning_seen:
                result["trace_warning"] = ctx.tracer.trace_failures
                ctx.trace_warning_seen = ctx.tracer.trace_failures
            return result

    def _run(self, ctx: Context, name: str, arguments: dict | None, audience: str) -> dict:
        game = ctx.game
        if audience != "engine" and name not in ("close_turn", "finalize_talk") and state.read(game) is not None and state.is_over(game):
            return error("game over")
        tool = self._tools.get(name) if isinstance(name, str) else None
        current = interaction.opened(game)
        if audience == "gm" and current and name not in ("finalize_talk", "notebook"):
            return error("finish the conversation first", interaction_id=current["interaction_id"])
        if audience == "gm" and ctx.turn_open and name not in ("player_turn", "close_turn", "finalize_talk", "notebook"):
            key = ("gm", state.read(game)["turn"])
            ctx.counts[key] = ctx.counts.get(key, 0) + 1
            if ctx.counts[key] > 3:
                return error("game master tool limit")
        if audience == "npc" and name not in ("npc_context", "submit_draft"):
            # Bind failed calls to the current valid attempt before checking the
            # gameplay schema, so malformed arguments cannot evade the cap.
            args = arguments if isinstance(arguments, dict) else {}
            iid, attempt = args.get("interaction_id"), args.get("attempt")
            if isinstance(iid, str) and type(attempt) is int:
                problem = interaction.validate(game, iid, attempt, ("drafting",))
                if problem:
                    return error(problem)
                key = (iid, attempt)
                ctx.counts[key] = ctx.counts.get(key, 0) + 1
                if ctx.counts[key] > 4:
                    ctx.limited.add(key)
                    return error("NPC tool limit")
        if tool is None or tool.audience != audience:
            return error(f"unknown tool {name!r}", tools=self.names(audience))
        problem = _check_arguments(tool.input_schema, arguments)
        if problem:
            return error(problem)
        game = ctx.game
        started = state.read(game) is not None
        if name != "start_run" and not started:
            return error("run not started")
        if started and not tool.when_ended:
            if state.is_over(game):
                return error("game over")
            if rules.past_end(game) and audience not in ("npc", "referee") and name != "finalize_talk":
                # NPC tools only run inside a talk that started before the end of the day
                return error("the day is over")
        return tool.func(ctx, **(arguments or {}))


def _check_arguments(schema: dict, arguments: Any) -> str | None:
    if arguments is None:
        arguments = {}
    if not isinstance(arguments, dict):
        return "arguments must be an object"
    props = schema["properties"]
    for key in schema["required"]:
        if key not in arguments:
            return f"missing argument {key!r}"
    for key, value in arguments.items():
        if key not in props:
            return f"unexpected argument {key!r}"
        want = _JSON_TYPES.get(props[key].get("type"))
        if want and (not isinstance(value, want) or isinstance(value, bool)):
            return f"argument {key!r} must be of type {props[key]['type']}"
        if want is list and props[key].get("items", {}).get("type") == "string" \
                and not all(isinstance(v, str) for v in value):
            return f"argument {key!r} must be a list of strings"
        if "enum" in props[key] and value not in props[key]["enum"]:
            return f"argument {key!r} must be one of {props[key]['enum']}"
    return None


_savepoints = itertools.count(1)
REGISTRY = Registry()
