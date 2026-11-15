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
| 8 | M1 | When is a find stamped, before or after the action's time cost? | After: `look` and `talk` advance the clock first, so the notebook shows the time the action ends, as in STRUCTURE example 6 (its example 1 lists the events the other way round). |
| 9 | M1 | What if a tool fails half-way? | Each registry call runs in a SQLite savepoint. An exception or an `ok: false` result rolls back everything the call wrote, so an error always means nothing changed. |
| 10 | M1 | Can actions start after 18:00 inside one turn? | No. Once the clock reaches the end of the day every tool returns `the day is over`; `end_turn` then ends the game. An action that starts before 18:00 may finish after it. |
| 11 | M2 | Spans versus tool rollback | Spans share the run database, so the tracer holds closed spans in memory and writes them (table and JSONL) when the root span closes, after any savepoint is released. A rejected draft inside a failed tool call is kept. |
| 12 | M2 | What is a trace? | One trace per turn (`<run_id>-t0001`, ...); work before the first turn is in `<run_id>-setup`. A `Context` always has a tracer, so no tool call can run untraced; there is one tracer per run (kept on `Game`), span ids are `<run_id>-sNNNNNN`, and a child span stays in its parent's trace. A failure to write a span is reported on stderr and never reaches the tool caller. |
| 13 | M2 | Message format between agents and `LLMClient` | A neutral dict format (`role` user / assistant / tool, `tool_calls`), documented in `llm/base.py`. Each client converts it to its provider's format. |
| 14 | M2 | Per-run JSONL files in `traces/` | Written there as the brief says, but ignored by git (`traces/*.jsonl`); the saved examples chosen in M6 are added with `git add -f`. |
| 15 | M3 | How can the game be played and tested without a model? | `offline.py` holds deterministic `ScriptedLLM` policies: a game master that understands typed commands and NPCs that recall, reveal what the engine unlocks and repeat the engine's pointer lines. `harbor play` uses them by default; `harbor play --hosted` uses `HostedLLM` for the game master, the NPCs and the referee. Both go through the same agents, tools and spans. |
| 16 | M3 | Where is a game wired together? | `session.py` (not in STRUCTURE): creates the run, tracer, NPC runner and game master. Used by the CLI, the bot and the tests. |
| 17 | M3 | Typed command grammar | `talk <person>: <words>`, `ask <person>: <words>`, `hint`, `accuse <person>: <motive> C3 C5`, plus `look`, `go <place>`, `notebook`. |
| 18 | M3 | Trust rules in detail | Levels 0 to 2, at most one change per conversation. `polite_repeat` needs an earlier line from the player to that character today and counts once per character. `related_clue_shown` needs a notebook clue named in the current message (by keyword) that is a `related_clues` entry for that character, and counts once per clue. `rude` lowers by one. `unlock_if_shown` counts clues shown to that character in any message today. |
| 19 | M3 | Re-revealing a clue already in the notebook | `reveal_clue` returns ok with `already_known: true` and logs `clue_confirmed`, so the referee knows that this character said it this turn. |
| 20 | M3 | What the game master returns when its loop fails | Over the 3-call cap: a fixed line asking the player to do one thing at a time. With no narration text: the tool results rendered plainly. `end_turn` runs in both cases. |
| 21 | M3 | Agent code inside `talk` | The write guard is closed while the NPC runner runs; only its own registry calls reopen it. A runner exception, an empty reply or the 4-call cap gives the character's `fallback_line` and `fallback: true`. |
| 22 | M3 | `HostedLLM` settings | `max_tokens` 4096; no thinking or sampling parameters, so any current model accepts the request; a `refusal` stop gives an empty reply, so `talk` uses the fallback line. The server-side refusal fallback is not set because the model is the owner's choice (`HARBOR_MODEL`). Assistant turns replay the provider's own content blocks unchanged. |
| 23 | M3 | `runs.model` | The game master's model name; `runs.prompt_hash` is the first 16 hex digits of SHA-256 over the three prompt files. |
