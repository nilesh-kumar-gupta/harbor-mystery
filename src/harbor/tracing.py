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
import sys
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

    seq: int
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
        self.trace_failures = 0
        self._virtual: dict[str, Span] = {}
        self._closed_virtual = set()
        self._stack: list[Span] = []
        self._pending: list[tuple[Span, float]] = []
        # continue numbering after spans already in this database for this run
        self._count = conn.execute(
            "SELECT COUNT(*) FROM spans WHERE run_id = ?", (run_id,)).fetchone()[0]
        if self.jsonl_path:
            self.jsonl_path.parent.mkdir(parents=True, exist_ok=True)

    def begin_trace(self, label: str) -> str:
        """Start a new trace (the game uses one per turn). Returns its id.

        Only between root spans: a trace must not change under an open span.
        """
        if self._stack:
            raise RuntimeError("begin_trace called while a span is open")
        self.trace_id = f"{self.run_id}-{label}"
        return self.trace_id

    def current(self) -> Span | None:
        return self._stack[-1] if self._stack else None

    def _parent_trace(self, parent_id):
        for span in [*self._virtual.values(), *self._stack, *(s for s, _ in self._pending)]:
            if span.span_id == parent_id:
                return span.trace_id
        row = self.conn.execute("SELECT trace_id FROM spans WHERE run_id=? AND span_id=?",
                                (self.run_id, parent_id)).fetchone()
        return row[0] if row else self.trace_id

    @contextmanager
    def span(self, kind: str, name: str, input: Any = None, attrs: dict | None = None,
             parent: Span | None = None, *, parent_id: str | None = None,
             trace_id: str | None = None, span_id: str | None = None) -> Iterator[Span]:
        """Record the enclosed work as one span. An exception marks it `error` and passes on."""
        if kind not in KINDS:
            raise ValueError(f"unknown span kind {kind!r}")
        if parent is None:
            parent = self.current()
        self._count += 1
        span = Span(
            seq=self._count, span_id=span_id or f"{self.run_id}-s{self._count:06d}",
            parent_span_id=parent_id if parent_id is not None else parent.span_id if parent else None,
            trace_id=trace_id or (self._parent_trace(parent_id) if parent_id is not None else parent.trace_id if parent else self.trace_id), kind=kind, name=name, start_ts=self._clock(),
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

    def open_virtual(self, kind: str, name: str, span_id: str, *, parent_id=None,
                     trace_id=None, input=None, attrs=None) -> Span:
        """Open a span across requests without keeping a Python stack open."""
        if span_id in self._virtual:
            return self._virtual[span_id]
        if span_id in self._closed_virtual or self.conn.execute(
                "SELECT 1 FROM spans WHERE run_id=? AND span_id=?", (self.run_id, span_id)).fetchone():
            raise ValueError("virtual span already closed")
        if kind not in KINDS:
            raise ValueError(f"unknown span kind {kind!r}")
        self._count += 1
        span = Span(self._count, span_id, parent_id, trace_id or (self._parent_trace(parent_id) if parent_id else self.trace_id),
                    kind, name, self._clock(), input=input, attrs=dict(attrs or {}))
        self._virtual[span_id] = span
        return span

    def close_virtual(self, span_id: str, *, status="ok", output=None, attrs=None) -> None:
        span = self._virtual.pop(span_id, None)
        if span is None:
            self.trace_failures += 1
            return
        self._closed_virtual.add(span_id)
        span.status, span.output = status, output
        span.attrs.update(attrs or {})
        self._pending.append((span, self._clock()))
        if not self._stack:
            self.flush()

    def amend(self, span: Span, **fields) -> None:
        """Change a closed span before it is written, e.g. mark a draft `rejected`
        once the referee has judged it. Closed spans wait for their root to close."""
        if not any(pending is span for pending, _ in self._pending):
            raise RuntimeError(f"span {span.span_id} is already written")
        for name, value in fields.items():
            setattr(span, name, value)

    def flush(self) -> None:
        """Write every closed span, in the order they were opened.

        Never raises: tracing must not turn a finished tool call into an exception.
        A span that cannot be written is reported on stderr and skipped.
        """
        pending, self._pending = self._pending, []
        for span, end_ts in sorted(pending, key=lambda item: item[0].seq):
            try:
                self._write(span, end_ts)
            except Exception as e:
                self.trace_failures += 1
                print(f"harbor: could not write span {span.span_id}: {e}", file=sys.stderr)

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
        line = None
        if self.jsonl_path:  # serialise first, so a bad value fails before either write
            line = json.dumps(dict(row, input=span.input, output=span.output, attrs=span.attrs),
                              default=str) + "\n"
        self.conn.execute(
            f"INSERT INTO spans ({', '.join(COLUMNS)}) VALUES ({', '.join('?' * len(COLUMNS))})",
            [row[c] for c in COLUMNS],
        )
        if line is not None:
            with self.jsonl_path.open("a", encoding="utf-8") as f:
                f.write(line)
