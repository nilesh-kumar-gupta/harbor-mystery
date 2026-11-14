# ADR 002: Small decisions made while building M1 to M5

Date: 2026-11-15. Status: proposed (written by the build agent; owner to accept or amend).

## Context

The brief, ADR 001 and `docs/STRUCTURE.md` leave some details open. Each row below is a choice made during the build so the code and tests could be finished. None reopens a settled decision.

## Decisions

| # | Milestone | Question | Decision |
| --- | --- | --- | --- |
| 1 | M1 | How is "state changes only inside a tool call" enforced? | `events.record` is the only writer of run tables and raises `WriteOutsideTool` unless the registry has a tool call open (`Game.tool_depth`). Replay uses the same reducer (`events.apply`). |
| 2 | M1 | Creating the first `game_state` row is a state change with no tool | A `start_run` engine step, registered like `end_turn` (ADR 001 #8) with audience `engine`. Engine steps live in `tools/steps.py` and are never offered to a model. |
| 3 | M1 | Where do the case's clocks, start place and nudge order live in the database? | A `case_meta` table (one row). `case_solution` also carries `min_supporting`. |
| 4 | M1 | Extra columns | `game_state.turn`, `nudge_clue`, `result`; `notebook.turn`; `events.seq`, `events.span_id`. |
| 5 | M1 | How does the no-model loop understand typed lines? | `commands.py` parses a small command grammar into a tool call and renders tool results as text. It holds no rules. |
| 6 | M1 | Name matching for `go` and `talk` | By id or name, ignoring case, punctuation and a leading "the"; a partial match counts only if unique. |
| 7 | M1 | Topic and keyword matching | A term matches at the start of a word ("boat" matches "boats"; "night" does not match "midnight"). |
