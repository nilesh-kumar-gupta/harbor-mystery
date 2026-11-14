# Harbor Mystery

A terminal detective game used as a test bed for a hand-written multi-agent architecture. The specification is `docs/BRIEF.md`; read it fully before changing code. `docs/STRUCTURE.md` explains the layout with worked traces, and `docs/adr/` records decisions (start with 001).

## Commands

```
python3 -m venv .venv && .venv/bin/pip install -e '.[dev]'   # first-time setup
.venv/bin/pytest                 # offline tests (default excludes live)
.venv/bin/pytest -m live         # model-backed checks; needs ANTHROPIC_API_KEY and HARBOR_MODEL
.venv/bin/harbor check-case      # validate cases/saltmere.json
.venv/bin/harbor play            # play by typing commands (no model); --seed N, --db PATH
```

## Architecture rules (enforced by tests, do not relax)

- Only `engine` writes to the database, and only inside a tool call. The model proposes; the engine validates and decides.
- Imports run one way: `cli` -> `agents` -> `tools` -> `engine`. `engine` never imports a model client.
- NPC tools take no character argument. They act on `game_state.active_character`, set by `talk`.
- Tools return errors as values (`{"ok": false, "error": "..."}`), never exceptions.
- `case_solution` is read only by scoring, never passed to an agent.
- Every model call, tool call and referee check goes through `tracing.span()`.
- No agent frameworks. Standard library plus `pytest`; `anthropic` only in `llm/hosted.py`.

## Working agreements

- Build in the brief's milestone order (M0 to M7). Commit after each milestone with tests passing, then stop and report: what works, what is next, any change from the brief.
- Do not add features outside the brief; put ideas in `docs/IDEAS.md`.
- Small decisions go in a short ADR (`docs/adr/NNN-title.md`, half a page).
- Keys come from environment variables and never enter the repo.
- Tests stay offline through `ScriptedLLM`; anything needing the hosted model is marked `live`.

## Who writes what

The owner writes prose, data and judgement calls; Claude writes code and tests. Do not draft these unless asked:

- Wording in `cases/saltmere.json` (descriptions, personas, speech styles, fallback lines). Keep IDs, knowledge rows and the solution as they are.
- Prompt files in `src/harbor/prompts/*.md` (Claude stubs them with the required tool names).
- ADRs on: event log versus mutable state, the referee design, the tool registry, the active-character rule.
- README prose, the demo transcript, and the choice of saved traces.

## Status

M0 done (scaffold, case loader, `harbor check-case`). M1 done (schema, state, event log and replay, rules, tool registry, `look` / `go` / `notebook`, engine steps `start_run` / `end_turn`, plain `harbor play` loop). Build decisions are in `docs/adr/002-build-decisions.md`. Next: M2, tracing and the model interface.
