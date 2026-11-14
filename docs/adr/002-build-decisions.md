# ADR 002: Small decisions made while building M1 to M5

Date: 2026-11-15. Status: proposed (written by the build agent; owner to accept or amend).

## Context

The brief, ADR 001 and `docs/STRUCTURE.md` leave some details open. Each row below is a choice made during the build so the code and tests could be finished. None reopens a settled decision.

## Decisions

| # | Milestone | Question | Decision |
| --- | --- | --- | --- |
| 1 | M1 | How is "state changes only inside a tool call" enforced? | `events.record` is the only writer of run state and raises `WriteOutsideTool` unless the registry has a tool call open (`Game.tool_depth`). Replay uses the same reducer. The one exception is the `runs` row, inserted once by `new_game` before the run starts. A test scans the source for SQL writes. |
| 2 | M1 | Creating the first `game_state` row is a state change with no tool | A `start_run` engine step, registered like `end_turn` (ADR 001 #8) with audience `engine`. Engine steps live in `tools/steps.py` and are never offered to a model. |
| 3 | M1 | Where do the case's clocks, start place and nudge order live in the database? | A `case_meta` table (one row). `case_solution` also carries `min_supporting`. |
| 4 | M1 | Extra columns | `game_state.turn`, `nudge_clue`, `result`; `notebook.turn`; `events.seq`, `events.span_id`. |
| 5 | M1 | How does the no-model loop understand typed lines? | `commands.py` parses a small command grammar into a tool call and renders tool results as text. It holds no rules. |
| 6 | M1 | Name matching for `go` and `talk` | By id or name, ignoring case, punctuation and a leading "the"; a partial match counts only if unique. |
| 7 | M1 | Topic and keyword matching | A term matches at the start of a word ("boat" matches "boats"; "night" does not match "midnight"). |
| 8 | M1 | When is a find stamped, before or after the action's time cost? | After: `look` advances the clock first, so the notebook shows 06:05 as in STRUCTURE example 6 (its example 1 lists the events the other way round). |
| 9 | M1 | What if a tool fails half-way? | Each registry call runs in a SQLite savepoint. An exception or an `ok: false` result rolls back everything the call wrote, so an error always means nothing changed. |
| 10 | M1 | Can actions start after 18:00 inside one turn? | No. Once the clock reaches the end of the day every tool returns `the day is over`; `end_turn` then ends the game. An action that starts before 18:00 may finish after it. |
| 11 | M2 | Spans versus tool rollback | Spans share the run database, so the tracer holds closed spans in memory and writes them (table and JSONL) when the root span closes, after any savepoint is released. A rejected draft inside a failed tool call is kept. |
| 12 | M2 | What is a trace? | One trace per turn (`<run_id>-t0001`, ...); work before the first turn is in `<run_id>-setup`. A `Context` always has a tracer, so no tool call can run untraced. |
| 13 | M2 | Message format between agents and `LLMClient` | A neutral dict format (`role` user / assistant / tool, `tool_calls`), documented in `llm/base.py`. Each client converts it to its provider's format. |
