"""The tool registry: name -> function + JSON schema, and the one way to call a tool.

`Registry.call` is the only door into the engine. It checks the caller may use
the tool, checks the arguments against the schema, opens the write guard, and
turns any exception into an error value (rolling back what the call wrote), so
an agent never sees a traceback and an error always means nothing changed.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable

from harbor.engine import rules, state
from harbor.engine.state import Game

AUDIENCES = ("gm", "npc", "engine")  # engine steps are never offered to a model
_JSON_TYPES = {"string": str, "array": list, "integer": int, "object": dict}


@dataclass
class Context:
    """What a tool function receives: the run, and (later milestones) its collaborators."""

    game: Game


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
        """Run one tool for a caller of the given audience.

        Never raises: every failure comes back as {"ok": false, "error": ...}. A call
        is all or nothing: if the tool fails or returns an error value, whatever it
        wrote is rolled back, so an error always means "nothing changed".
        """
        game = ctx.game
        savepoint = f"tool_{game.tool_depth}"
        game.conn.execute(f"SAVEPOINT {savepoint}")
        game.tool_depth += 1
        try:
            result = self._run(ctx, name, arguments, audience)
        except Exception as e:  # errors are values: the model must be able to recover
            result = error(f"internal error in {name}: {type(e).__name__}: {e}")
        finally:
            game.tool_depth -= 1
        if not result.get("ok"):
            game.conn.execute(f"ROLLBACK TO {savepoint}")
        game.conn.execute(f"RELEASE {savepoint}")
        return result

    def _run(self, ctx: Context, name: str, arguments: dict | None, audience: str) -> dict:
        tool = self._tools.get(name) if isinstance(name, str) else None
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
            if rules.past_end(game):
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


REGISTRY = Registry()
