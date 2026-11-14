"""The one span() helper. Every model call, tool call, referee check and agent
run is recorded through it, into the `spans` table and a JSONL mirror.

A span is complete when it closes; closed spans are held in memory and written
when the root span of the stack closes. That ordering matters: a tool call that
fails is rolled back (a SQLite savepoint), and its child spans, such as a
rejected NPC draft, must survive that rollback. Nesting gives the parent link:
a span opened while another is open becomes its child unless a parent is named.
"""

from __future__ import annotations

import json
import time
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Iterator

KINDS = ("agent", "llm_call", "tool_call", "referee")
COLUMNS = ("run_id", "trace_id", "span_id", "parent_span_id", "kind", "name", "start_ts",
           "end_ts", "model", "input_tokens", "output_tokens", "input", "output", "status",
           "attrs")


@dataclass
class Span:
    """An open span. Set `output`, `status`, token counts or attrs before it closes."""

    span_id: str
    parent_span_id: str | None
    trace_id: str
    kind: str
    name: str
    start_ts: float
    input: Any = None
    output: Any = None
    status: str = "ok"          # ok | error | rejected
    model: str | None = None
    input_tokens: int | None = None
    output_tokens: int | None = None
    attrs: dict = field(default_factory=dict)


class Tracer:
    def __init__(self, conn, run_id: str, jsonl_path: str | Path | None = None,
                 clock: Callable[[], float] = time.time) -> None:
        self.conn = conn
        self.run_id = run_id
        self.jsonl_path = Path(jsonl_path) if jsonl_path else None
        self.trace_id = f"{run_id}-setup"   # one trace per turn; see begin_trace
        self._clock = clock
        self._stack: list[Span] = []
        self._pending: list[tuple[Span, float]] = []
        self._count = 0
        if self.jsonl_path:
            self.jsonl_path.parent.mkdir(parents=True, exist_ok=True)

    def begin_trace(self, label: str) -> str:
        """Start a new trace (the game uses one per turn). Returns its id."""
        self.trace_id = f"{self.run_id}-{label}"
        return self.trace_id

    def current(self) -> Span | None:
        return self._stack[-1] if self._stack else None

    @contextmanager
    def span(self, kind: str, name: str, input: Any = None, attrs: dict | None = None,
             parent: Span | None = None) -> Iterator[Span]:
        """Record the enclosed work as one span. An exception marks it `error` and passes on."""
        if kind not in KINDS:
            raise ValueError(f"unknown span kind {kind!r}")
        if parent is None:
            parent = self.current()
        self._count += 1
        span = Span(
            span_id=f"s{self._count:05d}",
            parent_span_id=parent.span_id if parent else None,
            trace_id=self.trace_id, kind=kind, name=name, start_ts=self._clock(),
            input=input, attrs=dict(attrs or {}),
        )
        self._stack.append(span)
        try:
            yield span
        except BaseException as e:
            span.status = "error"
            span.attrs["error.type"] = type(e).__name__
            raise
        finally:
            self._stack.pop()
            self._pending.append((span, self._clock()))
            if not self._stack:
                self.flush()

    def flush(self) -> None:
        """Write every closed span, in the order they were opened."""
        pending, self._pending = self._pending, []
        for span, end_ts in sorted(pending, key=lambda item: item[0].span_id):
            self._write(span, end_ts)

    def _write(self, span: Span, end_ts: float) -> None:
        dump = lambda value: None if value is None else json.dumps(value, default=str)
        row = {
            "run_id": self.run_id, "trace_id": span.trace_id, "span_id": span.span_id,
            "parent_span_id": span.parent_span_id, "kind": span.kind, "name": span.name,
            "start_ts": span.start_ts, "end_ts": end_ts, "model": span.model,
            "input_tokens": span.input_tokens, "output_tokens": span.output_tokens,
            "input": dump(span.input), "output": dump(span.output), "status": span.status,
            "attrs": json.dumps(span.attrs, default=str),
        }
        self.conn.execute(
            f"INSERT INTO spans ({', '.join(COLUMNS)}) VALUES ({', '.join('?' * len(COLUMNS))})",
            [row[c] for c in COLUMNS],
        )
        if self.jsonl_path:
            line = dict(row, input=span.input, output=span.output, attrs=span.attrs)
            with self.jsonl_path.open("a", encoding="utf-8") as f:
                f.write(json.dumps(line, default=str) + "\n")
