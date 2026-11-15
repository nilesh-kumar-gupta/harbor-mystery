# ADR 004: M7, the Claude Code plugin and the interaction lifecycle

Date: 2026-11-15. Status: proposed, owner's answers to the open questions recorded in section 7 (Phase 0 design; nothing is implemented yet).

Inputs: `docs/BRIEF.md` (M7), `docs/M7_ARCHITECTURAL_BRIEF.md` with its amendments (the amendments win where they disagree), ADR 001 to 003, the owner's M7 kickoff decisions (one lifecycle, draft agent files, the owner plays the real run, minimal evaluation, `v0.1.0` after M7).

## 1. What Claude Code can and cannot do (verified 2026-11-15, Claude Code 2.1.325)

| # | Finding | Source | Confidence |
| --- | --- | --- | --- |
| C1 | A subagent can spawn subagents, up to 3 levels by default (`CLAUDE_CODE_MAX_SUBAGENT_SPAWN_DEPTH`). The game master is still the **main session**: a subagent cannot hold a turn-by-turn conversation with the player. | code.claude.com/docs/en/sub-agents | High |
| C2 | A subagent's `tools` field is an allowlist and may name MCP tools by full name; a plugin server's tools are named `mcp__plugin_<plugin>_<server>__<tool>` (here `mcp__plugin_harbor-mystery_harbor__<tool>`). Without MCP entries in `tools`, a subagent gets no MCP tools. | docs/en/sub-agents, docs/en/mcp, docs/en/plugins/components | High (naming); medium-high (per-tool entries are documented by reference, not by example) |
| C3 | Plugin agents ignore `mcpServers`, `hooks`, `permissionMode`, `initialPrompt`. Supported: `name`, `description`, `model`, `effort`, `maxTurns`, `tools`, `disallowedTools`, `skills`, `memory`, `background`, `omitClaudeMd`, `isolation`, `color`. `claude plugin validate --strict` does **not** warn about ignored fields (a probe agent with `mcpServers`, `permissionMode` and an unknown key passed). | docs/en/plugins/components; CLI probe in scratchpad | High |
| C4 | `claude --agent harbor-mystery:game-master` runs the **main thread** as a plugin agent: its `tools` allowlist and system prompt apply, and `tools: Agent(npc, referee), ...` limits which subagents it may spawn. This allowlist form works only for the main thread. | docs/en/sub-agents ("--agent", "Agent(agent_type)") | High (documented), untested here |
| C5 | Nothing tells the MCP server which agent made a call. One server process serves the session; the only documented request metadata is `_meta["io.modelcontextprotocol/clientCapabilities"]`. **So the server cannot distinguish game master, NPC and referee calls.** The referee verdict is advisory; Python validation is the enforcement boundary. | docs/en/mcp (full page searched) | Medium-high (absence in docs; T09 logs every request's `_meta` so the owner's run settles it) |
| C6 | A subagent returns only its final text to the parent; there is no schema-checked structured output for the Agent tool. The verdict therefore travels through an MCP tool (`submit_verdict`) whose argument Python validates. | docs/en/sub-agents | Medium-high |
| C7 | A command's or skill's `allowed-tools` pre-approves tools; it does **not** restrict them. Restriction comes from an agent's `tools` (C2, C4). | docs/en/skills | High |
| C8 | A plugin with a relative source (`"./"`) in a marketplace added from a local path loads **in place**; `CLAUDE_PLUGIN_ROOT` is the repo, and edits apply at the next session or `/reload-plugins`. MCP stdio servers receive `CLAUDE_PLUGIN_ROOT`, `CLAUDE_PLUGIN_DATA`, `CLAUDE_PROJECT_DIR`. | docs/en/plugins/loading, manifest-reference | High |
| C9 | MCP tools are deferred behind tool search by default; `_meta["anthropic/alwaysLoad"]: true` on a tool loads it upfront. Output over 25k tokens is cut; tool timeout defaults to ~28 h. | docs/en/mcp | High |
| C10 | Formats: `.claude-plugin/plugin.json` (only `name` required); `.claude-plugin/marketplace.json` (`name`, `owner.name`, `plugins[]` with `name` and `source`; `"./"` = the repo root); `agents/*.md` and `commands/*.md` with YAML frontmatter. Plugin commands are namespaced: `commands/harbor-mystery.md` is documented as `/harbor-mystery:harbor-mystery`; the bare `/harbor-mystery` is documented only for unambiguous renamed skills. | docs/en/plugins/*, docs/en/skills | High (format); low (bare name, checked at install) |
| C11 | `claude plugin validate --strict <path>`: with both manifests in one folder, `validate .` checks only `marketplace.json`, so `.claude-plugin/plugin.json` must be validated too. It checks manifest types and fields, component paths (exist, inside root, no `..`), MCP entries, and reports unknown fields as warnings (`--strict` fails on them). It also prints non-blocking "plugin directory" findings (README, LICENSE, icon) unless `--profile directory`. | `claude plugin validate --help`; probe output | High |
| C12 | Subagents load CLAUDE.md unless `omitClaudeMd: true`; a main session started in this repo can `Read` `cases/saltmere.json` (the solution) unless its tools are restricted (C4). | docs/en/sub-agents | High |
| C13 | No dependency is needed: an MCP stdio server is newline-delimited JSON-RPC 2.0 (`initialize`, `tools/list`, `tools/call`, `ping`), written with the standard library. System `python3` is 3.15. | MCP spec 2025-06-18 (to re-check in T09); `python3 --version` | High |

## 2. Decisions

### 2.1 Responsibilities and module boundaries

Claude Code runs the agents; Python owns state and decides. The lifecycle lives in the engine, is exposed as tools, and is driven by one of two orchestrators: Claude Code (plugin) or `agents/conversation.py` with `ScriptedLLM` (offline, tests, `harbor play`, `harbor bot`). There is one `talk` lifecycle; the synchronous `talk()` tool is removed.

| Module | Change | Owns |
| --- | --- | --- |
| `engine/interaction.py` (new) | new | The state machine, the staging overlay, commit and reject, recovery. No model, no tools. |
| `engine/schema.py`, `engine/events.py` | extend | Tables `interactions`, `attempts`; their event types in the reducer; both in `HASHED_TABLES`. |
| `referee.py` | change | `code_check` (unchanged rules), `referee_context` (was `model_check_input`), `parse_verdict` (strict). No model call. |
| `tools/talk_tools.py` (new) | new | `begin_talk`, `finalize_talk` (gm); `npc_context`, `submit_draft` (npc); `referee_context`, `submit_verdict` (referee). |
| `tools/npc_tools.py` | change | The four NPC tools take `interaction_id` and `attempt`; reveals and trust steps are staged. |
| `tools/turn_tools.py` (new) | new | `player_turn`, `close_turn` (gm); the per-turn game master call cap. |
| `tools/session_tools.py` (new) | new | `new_game`, `resume_game`, `game_status` (gm, plugin only). |
| `tools/gm_tools.py` | change | `talk`, `_npc_reply`, `_draft` removed; game master tools refuse while a conversation is open. |
| `tools/registry.py` | change | Audience `referee` added; `Context` loses `npc_runner` and `referee_llm`; `Draft` removed; call caps kept per context. |
| `agents/conversation.py` (new) | new | Offline orchestrator: begin, NPC loop, submit, referee, verdict, finalize, retry. |
| `agents/npc.py`, `agents/game_master.py`, `agents/checker.py` (new) | change | NPC loop bound to an interaction; game master exposes a virtual `talk` that runs `conversation`; `checker` is the offline referee model call. |
| `tracing.py` | change | Explicit `parent_id`/`trace_id`, deferred ("virtual") spans for work done outside Python, a failure counter. |
| `mcp_server.py` (new, `src/harbor/`) | new | Stdio JSON-RPC; maps registry tools to MCP tools; derives the audience from the tool, never from the caller. |
| Plugin files (new, repo root) | new | `.claude-plugin/plugin.json`, `.claude-plugin/marketplace.json`, `commands/harbor-mystery.md`, `agents/game-master.md`, `agents/npc.md`, `agents/referee.md`. |

Import rules stay one way. `mcp_server` is an interface layer like `cli`: it may import `session`, `tools`, `tracing`, never `agents`. `agents/conversation.py` uses tools only, never `engine`.

### 2.2 Interaction state machine (one machine, replacing both proposals)

Interaction status: `pending -> drafting -> evaluating -> committed`, or `-> abandoned`. Each interaction has 1 to 3 attempts; the interaction's status is its current attempt's phase until it is committed.

```
begin_talk ──> PENDING ──npc_context──> DRAFTING ──submit_draft──> EVALUATING ──finalize_talk──┐
                 ^   (attempt n)          (NPC tools, staged)        (referee tools)           │
                 │                                                                              ▼
                 │                       accepted (code check pass AND verdict OK) ──> COMMITTED (reply)
                 └──── rejected/invalid/no draft, n < 3: attempt n+1, staged actions dropped
                       rejected/invalid/no draft, n = 3:              ──> COMMITTED (fallback line)
 resume_game on an open interaction:                                  ──> COMMITTED (fallback line, recovered)
```

- `PENDING` is the amendments' state, kept for one reason: the NPC fetches its own card with `npc_context`, so the game master passes only `interaction_id` and `attempt`, never persona or knowledge. `finalize_talk` may be called from `PENDING` or `DRAFTING` (the NPC failed or never started): the attempt closes as `no_draft`.
- Attempt outcomes: `accepted`, `code_leak`, `model_leak`, `invalid_verdict`, `no_verdict`, `no_draft`, `tool_limit`, `interrupted`. All but `accepted` reject.
- At most one open interaction per run. While one is open, every game master tool except `finalize_talk`, `game_status` and `notebook` returns `{"ok": false, "error": "finish the conversation first", "interaction_id": ...}`.
- Three attempts in all (one draft, two regenerations), then the character's `fallback_line`, with no model call. Retries cost no game time, as now.

### 2.3 Persistence and schema

Everything goes through the event log, so replay and the state hash cover the lifecycle. New tables, written only by the reducer:

```sql
CREATE TABLE interactions (
  run_id TEXT NOT NULL, interaction_id TEXT PRIMARY KEY, turn INTEGER NOT NULL,
  character_id TEXT NOT NULL, player_message TEXT NOT NULL,
  status TEXT NOT NULL,              -- pending | drafting | evaluating | committed | abandoned
  attempt INTEGER NOT NULL, reply TEXT, fallback INTEGER, outcome TEXT, new_clues TEXT);
CREATE TABLE attempts (
  interaction_id TEXT NOT NULL, attempt INTEGER NOT NULL,
  status TEXT NOT NULL,              -- pending | drafting | evaluating | closed
  note TEXT,                         -- the note shown to a regenerated draft (LEAK_NOTE / CHECK_FAILED_NOTE)
  staged TEXT NOT NULL,              -- JSON list of {"action": "reveal_clue"|"update_trust", "args": {...}}
  draft TEXT, verdict TEXT,          -- OK | LEAK | INVALID | NULL
  verdict_reason TEXT, outcome TEXT, leaked TEXT,
  PRIMARY KEY (interaction_id, attempt));
```

New event types: `interaction_begun`, `attempt_started`, `npc_started`, `action_staged`, `draft_submitted`, `verdict_recorded`, `attempt_closed`, `interaction_committed`, `interaction_recovered`. The game-state events are unchanged (`clue_found`, `clue_confirmed`, `trust_changed`, `line_said`, ...); they are written only at commit.

`interaction_id` is `i<NNN>-<4 hex>` from `Game.rng` (seeded), so two runs with one seed match. It is a lookup key, not a credential.

`begin_talk` commits what belongs to the player's action at once, as `talk` does now: clock +10, `talk_started` (sets `active_character`), the player's `line_said`, `clues_shown`, `interaction_begun`, `attempt_started(1)`.

### 2.4 Staged mutations and idempotent finalization

- **Staging.** `reveal_clue` and `update_trust` validate with the existing rules, then append an `action_staged` event (the action and its arguments) instead of `clue_found` or `trust_changed`.
- **Overlay.** Every NPC and referee tool, and `finalize_talk`, runs inside `interaction.overlay(game, attempt)`: a SAVEPOINT that lives for that one tool call only, replays the attempt's staged actions through the same rule functions (`rules.check_reveal` + `state.add_clue`, `rules.trust_step` + `state.change_trust`), runs the tool, then rolls back. So an NPC sees its own staged trust step (the guarded-clue flow keeps working), and no savepoint is ever held across an agent handoff.
- **Finalize** (one `finalize_talk` call = one registry savepoint = one short transaction):
  1. Unknown id: error. Terminal interaction: return the stored outcome with `already_finalized: true` and write nothing (idempotent). Not the run's open interaction: error `stale interaction`.
  2. Close the current attempt: no draft gives `no_draft` (or `tool_limit`); otherwise, inside the overlay, re-run every staged action (this re-validates it), then `referee.code_check` on the draft. Accept only if the code check passes **and** the recorded verdict is `OK`.
  3. Accepted: replay the staged actions for real, `line_said(npc, draft)`, `attempt_closed(accepted)`, `interaction_committed`.
  4. Rejected: drop the overlay, `attempt_closed(<outcome>)`; with attempts left, `attempt_started(n+1, note)` and return `status: "retry"`; otherwise `line_said(npc, fallback_line)` and `interaction_committed(fallback)`.
- `submit_draft` and `submit_verdict` are once per attempt: the same value again returns the stored result; a different value is refused. A verdict before a draft is refused.

### 2.5 Authorization (said plainly)

The MCP server cannot tell which agent is calling (C5). Its guarantees come from server-owned state, not from caller identity:

1. **Enforced by Python, whoever calls:** the audience of a call is the tool's registered audience, never a caller argument. NPC tools work only on the run's open interaction, its current attempt, in `drafting`; they act on that interaction's character (which must equal `game_state.active_character`), so no call can read or reveal another character's knowledge. Referee tools work only in `evaluating`. Stale or finalized interactions and old attempts are refused. Caps: 4 NPC tool calls per attempt (`npc_context` and `submit_draft` not counted), 3 game master action calls per turn (`player_turn`, `close_turn`, `finalize_talk`, `game_status`, `notebook` not counted), counted per server process. `case_solution` is read only by scoring.
2. **Enforced by Claude Code configuration, not by Python:** which agent may call which tools (agent `tools` allowlists), and, in `--agent` launch mode (2.9), that the game master cannot call NPC or referee tools, read files or spawn other agents.
3. **Not enforced:** that a verdict came from the referee. In a plain session the game master could call `submit_verdict`. So the referee verdict is **advisory**: it can only veto. A forged `OK` skips the semantic check but cannot pass a draft that fails the deterministic code check, and cannot reveal anything the active character could not reveal through its own tools.

What remains probabilistic: a paraphrase that no clue keyword catches is stopped only by the referee model. The game master also receives the NPC subagent's final text; the NPC is told to end with `submitted` only, and the game master to narrate only from `finalize_talk`.

### 2.6 Strict referee verdict contract

- The referee subagent calls `submit_verdict(interaction_id, attempt, verdict, reason)`. `verdict` must be exactly `"OK"` or `"LEAK"`, compared after stripping whitespace, case-sensitive. Anything else is recorded as `INVALID` (with the raw value, cut to 200 characters) and fails closed. No verdict at finalize is `no_verdict` and fails closed.
- Offline, `agents/checker.py` asks the referee `ScriptedLLM` and passes its text through `referee.parse_verdict` (same rule: the whole reply, stripped, must be `OK` or `LEAK`; `"OK, but..."` is invalid). A model exception is recorded as verdict `INVALID` with `verdict_reason = "model_error: <type>"` and its `llm_call` span `error`.
- Traces keep these apart: `referee.code_check` (pass/leak), `referee.verdict` (OK/LEAK/INVALID/missing), `referee.verdict_source` (`mcp` or `offline`), `referee.model_error` (true/false).
- Regeneration notes: after `code_leak` or `model_leak` the next attempt gets `LEAK_NOTE`; after `invalid_verdict`, `no_verdict` or `no_draft` it gets `CHECK_FAILED_NOTE`. The note never carries clue ids or the referee's reason.

### 2.7 Retry and fallback

| Failure | Outcome | Next |
| --- | --- | --- |
| Code check finds a clue keyword the character may not say | `code_leak` | Retry with `LEAK_NOTE`; the verdict is not consulted |
| Verdict `LEAK` | `model_leak` | Retry with `LEAK_NOTE` |
| Verdict not `OK`/`LEAK`, or model error | `invalid_verdict` | Retry with `CHECK_FAILED_NOTE` |
| No verdict at finalize (referee subagent failed or skipped) | `no_verdict` | Retry with `CHECK_FAILED_NOTE` |
| No draft (NPC failed, empty reply) | `no_draft` | Retry with `CHECK_FAILED_NOTE` |
| 5th NPC tool call | refused; attempt flagged | `finalize_talk` closes it as `tool_limit` |
| Third attempt rejected | fallback line committed | Turn continues |
| Server restarted with an open interaction | `interrupted`, fallback line committed by `resume_game` | Turn continues |

ADR 002 #27 changes: an empty or failed draft is now retried (it used to go straight to the fallback). Offline the scripted NPC never fails, so behaviour is unchanged.

### 2.8 Tool contracts (MCP and registry are the same list)

Every result is JSON; errors are `{"ok": false, "error": ...}`. NPC and referee tools all take `interaction_id` (string) and `attempt` (integer) first.

| Tool | Audience | Arguments | Result (ok) | Allowed when |
| --- | --- | --- | --- | --- |
| `new_game` | gm | `seed?` | `run_id, title, premise, place, clock, help` | no game loaded, or the current one ended |
| `resume_game` | gm | `run_id?` (default latest) | `run_id, recovered[], summary` | plugin only |
| `game_status` | gm | none | `run_id, turn, open_interaction, db_path, trace_path, trace_failures` | always |
| `player_turn` | gm | `line` | `turn, trace_id, state` (+ the previous turn's `close_turn` result if it was left open) | game active |
| `close_turn` | gm | none | today's `end_turn` result | turn open, no open interaction |
| `look`, `go`, `notebook`, `hint`, `accuse` | gm | unchanged | unchanged | no open interaction (`notebook` always) |
| `begin_talk` | gm | `character, message` | `interaction_id, attempt: 1, character, clock, npc_agent, referee_agent` | character present, no open interaction |
| `finalize_talk` | gm | `interaction_id` | `status: "committed", reply, new_clues, clock, fallback?, attempts` or `status: "retry", attempt, reason_code` | see 2.4 |
| `npc_context` | npc | ids | `name, role, persona, speech_style, player_message, note?, calls_left` | `pending` or `drafting` |
| `recall_knowledge`, `recall_conversation` | npc | ids | unchanged | `drafting` |
| `reveal_clue` | npc | ids, `clue_id` | `id, text, staged: true` | `drafting` |
| `update_trust` | npc | ids, `reason` | `trust, staged: true` | `drafting` |
| `submit_draft` | npc | ids, `text` | `status: "evaluating"` | `drafting` |
| `referee_context` | referee | ids | `character, allowed_facts, may_send_the_player_to, player_said, facts_the_player_stated, reply` | `evaluating` |
| `submit_verdict` | referee | ids, `verdict, reason?` | `recorded: "OK"/"LEAK"/"INVALID"` | `evaluating`, once |

MCP mapping: `inputSchema` is the registry schema; every tool carries `_meta: {"anthropic/alwaysLoad": true}` (C9); the result is one text content block holding the JSON, with `isError` false (errors are values). Unknown JSON-RPC methods get a JSON-RPC error. The server handles one request at a time.

The offline game master keeps its typed-command tool `talk(character, message)`; `GameMaster` runs it as `conversation.run(...)`, which calls `begin_talk`, the NPC loop, `submit_draft`, `checker`, `submit_verdict`, `finalize_talk` (repeating on `retry`) and returns the committed result in today's `talk` shape. It counts as one game master call, like `begin_talk` in the plugin. The NPC model offline is offered the four NPC tools without the id arguments; the orchestrator adds them.

### 2.9 Plugin and subagent layout

```
.claude-plugin/plugin.json        name "harbor-mystery", version, description, author, license
                                  mcpServers.harbor: python3 -m harbor.mcp_server,
                                    env PYTHONPATH=${CLAUDE_PLUGIN_ROOT}/src, HARBOR_HOME=${CLAUDE_PLUGIN_DATA}
.claude-plugin/marketplace.json   name "harbor-mystery-local", owner, plugins: [{name "harbor-mystery", source "./"}]
commands/harbor-mystery.md        /harbor-mystery: new_game or resume_game, then the turn protocol
agents/game-master.md             tools: Agent(npc, referee) + the gm MCP tools (for --agent mode)
agents/npc.md                     tools: the 6 npc MCP tools only; omitClaudeMd: true; maxTurns: 10
agents/referee.md                 tools: referee_context, submit_verdict only; omitClaudeMd: true; maxTurns: 4
```

Each agent and command file starts with `<!-- DRAFT: owner to edit -->` (owner decision). The game master's turn protocol: `player_turn(line)`, at most 3 actions, for a conversation `begin_talk`, spawn `harbor-mystery:npc` in the foreground with only the ids, spawn `harbor-mystery:referee` with only the ids, `finalize_talk`, repeat the two spawns on `retry`, narrate only from tool results, `close_turn`. Runs and traces go to `$HARBOR_HOME/runs` and `$HARBOR_HOME/traces`.

Launch mode (owner question 1): recommended `claude --agent harbor-mystery:game-master` from an empty folder, then `/harbor-mystery`; Claude Code then enforces the game master's tool list (C4, C12). A plain `claude` session plus `/harbor-mystery` also works, but the game master then has every built-in tool (it could read `cases/saltmere.json`) and every MCP tool, and only the prompt holds it back.

### 2.10 Cross-agent trace correlation and persistence

- **Ids.** `run_id` (span column); `turn` (one trace per turn, `<run_id>-tNNNN`, as now); `game.interaction_id`, `game.attempt` attrs on every lifecycle span; `span_id`/`parent_span_id`. Ids cross the agent boundary explicitly: the game master passes `interaction_id` and `attempt` to the subagents in their prompts; the server rejects mismatches.
- **Virtual spans.** Work Python does not run is recorded as deferred spans with ids the server derives: `<run_id>-tNNNN-gm` (game master agent, opened by `player_turn`, written by `close_turn`), `<interaction_id>-aN-npc` (NPC agent, from `npc_context` to `submit_draft`; status `rejected` when its attempt is rejected) and `<interaction_id>-aN-ref` (referee, attrs as in 2.6). Each MCP tool call is a `tool_call` span whose parent is the matching virtual span. Offline, the same ids are used, and the real `llm_call` spans sit under them.
- **Shape of a plugin trace:** `agent game_master` > `tool_call player_turn`, `tool_call begin_talk`, `agent npc:mira (attempt 1)` > NPC `tool_call`s and `submit_draft` (raw draft in its input), `referee (attempt 1)` > `referee_context`, `submit_verdict`, then `tool_call finalize_talk` (outcome, committed events' `seq` range) and `tool_call close_turn`. Committed events carry their `finalize_talk` span id (`events.span_id`, as now).
- **Unavailable metadata.** Plugin mode has no `llm_call` spans, no model name and no token counts: those fields stay null and spans carry `harbor.model_metadata: "unavailable"`. Timing is server-side only (tool-call wall time; a virtual span's start is its first tool call). Every MCP request's `_meta` is stored on its span (`mcp.meta`) to settle C5.
- **Persistence.** Game state is committed when the tool call's savepoint is released; spans are buffered in memory and written after that, when the request's root span closes (as ADR 002 #11). So trace storage never decides game state. A virtual span is written at its close; if the process dies first, it is lost, and `resume_game` writes it from the durable lifecycle rows with status `interrupted` and `harbor.reconstructed: true` (start time unavailable). A failed SQLite or JSONL write goes to stderr (Claude Code's MCP log), increments `trace_failures` on the tracer, shows in `game_status` and on the next tool result as `trace_warning`; the run continues. A span is called durable only after its SQLite insert succeeded; the JSONL file is a mirror and may lag.

### 2.11 Release and scope

- `v0.1.0` is tagged by the owner after M7's real run, not at M6, and there is no `v0.2.0` in M7. This departs from the brief (M6 `v0.1.0`, M7 `v0.2.0`).
- No new dependency. No generic workflow engine: the lifecycle is one engine module and six tools.
- The brief's live smoke test (first words under 3 s, leak rate) becomes the owner's real run; first-word latency cannot be measured from the server and is reported as unavailable. A real-model eval suite goes to `docs/IDEAS.md`.

## 3. Phase plan (one plan, replacing both)

| Phase | Content | Ends with |
| --- | --- | --- |
| 0 | This design | Owner approval |
| 1 | Engine lifecycle, staging, strict verdict, tracer API, the offline orchestrator replacing `talk`, test migration, adversarial offline tests, regenerated example traces | Full suite green; `harbor play < docs/demo-commands.txt` and `harbor bot` output identical to the golden files; review |
| 2 | Session tools and recovery, the stdio MCP server, the trace report script | MCP protocol tests over a subprocess; recovery tests; review |
| 3 | Plugin files, agent and command drafts, validation, local marketplace install, install steps and the owner's checklist | `claude plugin validate --strict .` and `... .claude-plugin/plugin.json` pass; install from the repo works; `claude mcp list` shows the server connected; review |
| 4 | The owner's real run; trace analysis; fixes; docs (`CLAUDE.md`, `STRUCTURE.md`, `IDEAS.md`, trace notes) | Owner tags `v0.1.0` |

The amendments' Phase 3 (validation and observability) is folded into Phases 1 and 2 as tests; their Phase 4 (gameplay validation) is Phase 4 here.

## 4. Conflicts with the existing implementation

1. **`talk` is removed** from the registry; the brief's tool table, STRUCTURE examples 3 to 5 and the saved traces describe it. The offline game master's typed `talk` survives as an agent-level tool.
2. **Per-attempt savepoints** (ADR 002 #30) are replaced by staging plus the overlay; the per-call savepoint (ADR 002 #9) stays.
3. **`Context.npc_runner`, `Context.referee_llm`, `Draft`** disappear; `tests/test_agents.py`, `test_isolation.py`, `test_hints.py` and `traces/make_examples.py` use them and must be migrated (same assertions, new fixtures).
4. **Referee model check** parses `startswith("LEAK")` and treats anything else as OK (`referee._model_check`); it becomes strict (2.6). ADR 002 #26's fail-closed rule is kept.
5. **Registry `audience` argument** stays internal; at the MCP boundary it is derived from the tool. A new audience `referee` is added.
6. **`end_turn`** is an engine step "never offered to a model" (ADR 001 #8, `test_engine_steps_are_never_offered_to_a_model`). In the plugin the model is the loop, so a gm `close_turn` wraps it (owner question 2). The test changes to exempt exactly `close_turn`; `end_turn` itself stays engine-only.
7. **Caps.** The 3- and 4-call caps live in `agents/loop.py` today; they also move into the registry (per process) so the plugin is bounded by Python. Offline the loop still counts failed calls; the registry counts the same calls, so offline behaviour does not change.
8. **ADR 002 #27**: empty or failed drafts are retried before the fallback (2.7).
9. **Trace shape.** Plugin turns have no `llm_call` spans, so the brief's trace check (each turn has agent, tool_call, llm_call and referee spans) holds offline only. New tool spans (`begin_talk`, `submit_draft`, ...) change every saved example trace; they are regenerated, and `traces/README.md` (owner prose) needs the owner's edit.
10. **State hash and replay** include `interactions` and `attempts`; old run databases do not open with the new schema (no migration; runs are disposable).
11. **Case-file isolation in the plugin.** "No agent sees `case_solution`" holds through tools everywhere, but a game master in a plain session started in this repo can read the case file (C12). Only `--agent` mode, started outside the repo, enforces it.
12. **Layout.** STRUCTURE puts `mcp_server.py` at the root; it goes in `src/harbor/` so it imports the package without path tricks. `tests/test_architecture.py` gains a rule for it.
13. **Release numbering** (2.11). `pyproject.toml` already says `0.1.0`.
14. **Prompt files** are not changed: the offline game master still uses `talk`, and the NPC prompt's four tool names are unchanged. The plugin's instructions live in the agent and command drafts.

## 5. Test plan

All offline through `ScriptedLLM`, run by `.venv/bin/pytest`.

- **Golden** (`test_golden.py`): the demo game and the bot game print exactly what they print today (timestamps masked). Captured before any change.
- **Lifecycle** (`test_interaction_engine.py`, `test_lifecycle_tools.py`): valid lifecycle; unknown, stale and finalized interaction ids; NPC tool in the wrong state; wrong attempt; a 5th NPC call; duplicate `submit_draft` (same and different text); verdict before draft; duplicate and conflicting verdicts; duplicate `finalize_talk` writes nothing; rejected attempt leaves the state hash unchanged except lifecycle rows; staged trust visible to the same attempt's later calls and invisible to the next attempt; retry limit then fallback; game master tools refused while a conversation is open; replay of a log with interactions gives the same hash.
- **Verdict** (`test_referee_verdict.py`): `OK`, `LEAK` accepted; `ok`, `OK.`, `OK but`, `LEAK?`, empty, `None`, JSON, very long text all `INVALID`; model error vs invalid vs missing distinguishable in spans.
- **Adversarial** (`test_adversarial.py`), one test per amendment scenario: indirect reveal without exact keywords (caught by a scripted `LEAK` verdict, and shown to pass the code check, documenting the boundary); paraphrase; combining harmless facts; leading questions about another character; persona text that resembles a clue (not a leak); a rejected draft that staged a reveal and a trust step commits neither; an invalid referee answer never accepts; a forged `OK` with a keyword leak is still rejected; NPC tools called for another interaction or after finalize; NPC tries `reveal_clue` for an unknown clue and for the solution.
- **Recovery** (`test_recovery.py`): close and reopen the database mid-`drafting` and mid-`evaluating`; `resume_game` commits the fallback once, writes reconstructed virtual spans, and a late `finalize_talk` or `submit_draft` for the old interaction is refused or idempotent.
- **Tracing** (`test_tracing_correlation.py`): virtual span ids and parents; one interaction reconstructed from spans alone (decision, drafts, tool calls, verdicts, retry reasons, final reply, committed event seqs); a failing span write increments `trace_failures` and leaves state committed.
- **MCP** (`test_mcp_server.py`): subprocess over stdio: `initialize`, `tools/list` (names, schemas, `alwaysLoad`), `tools/call` for a full scripted conversation, error values, unknown method, malformed JSON line, `_meta` recorded.
- **Plugin files** (`test_plugin_files.py`): both manifests parse; every MCP tool named in an agent or command file exists with the right audience; NPC and referee agents list no built-in tools and set `omitClaudeMd`; each file's first line is the DRAFT marker. `claude plugin validate` itself is run by hand in Phase 3 (it is not called from pytest, to keep the suite free of the CLI).
- **Existing** tests keep their assertions; their fixtures move to the orchestrator.

## 6. Implementation tasks

Kinds: **M** = suits a mechanical implementer working from this spec (Codex or Claude); **J** = needs judgment about Claude Code itself (Claude). Each task leaves the full suite green on its own branch and runs `.venv/bin/pytest` and `.venv/bin/harbor bot` before handing back. Tasks in the same wave touch disjoint files and may run in parallel; a task starts only when its dependencies are merged into `main`.

| Id | Wave | Goal | Files it may create or change | Depends on | Acceptance checks | Kind |
| --- | --- | --- | --- | --- | --- | --- |
| M7-T01 | A | Freeze today's offline output as golden files | `tests/golden/demo.txt`, `tests/golden/bot.txt`, `tests/test_golden.py` | none | Golden tests pass on current `main`; run ids and timestamps masked | M |
| M7-T02 | A | Engine lifecycle: tables, events, reducer, `interaction.py` (begin, attempts, staging, overlay, commit, reject, fallback, recover) | `src/harbor/engine/schema.py`, `src/harbor/engine/events.py`, `src/harbor/engine/interaction.py`, `tests/test_interaction_engine.py` | none | Engine-level lifecycle tests from section 5; replay and hash include the new tables; `test_architecture.py` unchanged and green | M |
| M7-T03 | A | Strict verdict parsing and `referee_context` (additive; old `check` kept for now) | `src/harbor/referee.py`, `tests/test_referee_verdict.py` | none | Verdict tests from section 5 | M |
| M7-T04 | A | Tracer: explicit `parent_id`/`trace_id`, deferred spans with given ids, `trace_failures` counter | `src/harbor/tracing.py`, `tests/test_tracing_correlation.py` (tracer-only tests) | none | New tracer tests; existing `test_tracing.py` unchanged and green | M |
| M7-T05 | B | Switch to the one lifecycle: lifecycle, turn and NPC tools; remove `talk`; offline orchestrator, checker and caps; virtual spans; migrate existing tests | `src/harbor/tools/*.py` (except `session_tools.py`), `src/harbor/agents/*.py`, `src/harbor/referee.py` (remove `check`), `src/harbor/session.py`, `src/harbor/offline.py`, `src/harbor/bot.py` (only if needed), `tests/conftest.py`, `tests/test_agents.py`, `tests/test_isolation.py`, `tests/test_hints.py`, `tests/test_tracing.py`, `tests/test_architecture.py`, `tests/test_engine_tools.py`, `tests/test_lifecycle_tools.py` | T01, T02, T03, T04 | Whole suite green with the same assertions; golden tests pass unchanged; lifecycle-tool tests from section 5; `harbor bot` wins | M (largest task; a careful spec reader can do it, review closely) |
| M7-T06 | C | Adversarial offline tests | `tests/test_adversarial.py` | T05 | Every scenario in section 5 has a passing test | M |
| M7-T07 | C | `new_game`, `resume_game`, `game_status`; `Session.open`; recovery wiring and tests | `src/harbor/tools/session_tools.py`, `src/harbor/tools/__init__.py` (one import line), `src/harbor/session.py`, `tests/test_recovery.py` | T05 | Recovery tests from section 5 | M |
| M7-T08 | C | Regenerate the saved example traces with the new span shapes | `traces/make_examples.py`, `traces/example-*.jsonl` | T05 | Script runs; each example still shows what `traces/README.md` says it shows (list mismatches for the owner; do not edit the README) | M |
| M7-T09 | D | Stdio MCP server | `src/harbor/mcp_server.py`, `tests/test_mcp_server.py`, `tests/test_architecture.py` (add the `mcp_server` layer rule) | T07 | MCP tests from section 5; standard library only | M |
| M7-T10 | D | Plugin manifest, marketplace, `/harbor-mystery` command, game master, NPC and referee agent drafts | `.claude-plugin/plugin.json`, `.claude-plugin/marketplace.json`, `commands/harbor-mystery.md`, `agents/game-master.md`, `agents/npc.md`, `agents/referee.md`, `tests/test_plugin_files.py` | T05 (tool names frozen in 2.8) | Plugin-file tests; `claude plugin validate --strict .` and `claude plugin validate --strict .claude-plugin/plugin.json` pass | J |
| M7-T11 | D | Trace report: per-run metrics from a run database (interactions, completion, attempts per interaction, outcomes by type, fallback rate, invalid and missing verdicts, NPC tool calls, `_meta` seen) | `traces/report.py`, `tests/test_trace_report.py` | T05 | Report on a scripted game with a leak, a retry and a fallback gives the expected counts | M |
| M7-T12 | E | Local install and connection check; install steps and the owner's checklist (a normal conversation, a guarded clue, an attempted leak, a full game to accusation) | `docs/PLAY_IN_CLAUDE_CODE.md`; fixes to T09/T10 files if the install shows a problem | T09, T10 | `claude plugin marketplace add <repo>`, `claude plugin install harbor-mystery@harbor-mystery-local`, `claude plugin list` shows it enabled, `claude mcp list` shows `plugin:harbor-mystery:harbor` connected; no command that calls a model | J |
| M7-T13 | F | The real run | none (owner plays) | T12 | A game to accusation; the run database and traces kept | Owner |
| M7-T14 | F | Analyse the owner's traces; fix what they show; docs (`CLAUDE.md`, `docs/STRUCTURE.md`, `docs/IDEAS.md` with the real-model eval suite) | as needed, then the docs named | T13, T11 | Report of metrics from `traces/report.py`; C5 settled from `mcp.meta`; suite green | J |

Parallel sets: wave A (T01, T02, T03, T04); wave C (T06, T07, T08); wave D (T09, T10, T11; T10 and T11 may also start right after T05). T05 is the one serial bottleneck; splitting it would leave `main` red between merges because NPC tool signatures and `talk` change together.

## 7. Open questions for the owner

1. **Launch mode.** (a) `claude --agent harbor-mystery:game-master` from an empty folder, then `/harbor-mystery`: Claude Code enforces the game master's tools. (b) A plain session with `/harbor-mystery`: simpler, but the game master can read files and call every tool. *Recommend (a), and keep (b) working.*
2. **Closing a turn.** (a) A gm `close_turn` tool, plus `player_turn` closing a turn left open. (b) No model-facing turn end: `player_turn` closes the previous turn, so a timeout shows one line late. *Recommend (a).*
3. **Interrupted conversation.** (a) `resume_game` commits the character's fallback line. (b) Abandon it with no reply (the 10 minutes stay spent). (c) Resume the open attempt. *Recommend (a): deterministic and like any other failure.*
4. **Where runs and traces go in the plugin.** (a) `${CLAUDE_PLUGIN_DATA}` (`~/.claude/plugins/data/harbor-mystery-harbor-mystery-local/`), shown by `game_status`. (b) The folder Claude Code was started in. *Recommend (a); an env override `HARBOR_HOME` exists either way.*
5. **Command name.** (a) `commands/harbor-mystery.md`, documented as `/harbor-mystery:harbor-mystery`; the bare `/harbor-mystery` is checked at install. (b) A skill (the newer format) with the same name. *Recommend (a): matches STRUCTURE; switch if the bare name fails.*
6. **Who installs.** (a) Claude runs `claude plugin marketplace add` and `install` in Phase 3 (no model call; it writes your `~/.claude` settings, and `claude plugin uninstall` undoes it). (b) You install from the steps. *Recommend (a).*
7. **Agent models.** (a) Game master: session model; NPC: `sonnet`; referee: `haiku` (the brief's "cheap model check"). (b) All `inherit`. *Recommend (a).*
8. **`resume_game` scope.** (a) Minimal: latest run or a run id, recovery included. (b) None: every session starts a new game, and recovery runs only when the server restarts within a session. *Recommend (a): it is the natural home of recovery and costs little.*

### Owner answers (2026-11-15)

1. **Launch mode: (b), a plain session with `/harbor-mystery`.** The game master's restraint from reading `cases/saltmere.json` and from calling NPC or referee tools rests on its prompt only. `agents/game-master.md` is still written so that `--agent` mode works, but it is not the documented way to play. Section 2.5 point 2 and conflict 11 therefore describe a protection this launch mode does not use; the trust boundary for game state is unchanged (Python), but spoiler protection is prompt-only.
2. Closing a turn: (a), as recommended.
3. Interrupted conversation: (a), commit the fallback line.
4. Runs and traces: (a), `${CLAUDE_PLUGIN_DATA}` with the `HARBOR_HOME` override, as recommended.
5. Command: (a), as recommended.
6. **Who installs: (b), the owner** runs the install commands. M7-T12 prepares and documents them and checks the result after the owner runs them; it does not run `marketplace add` or `install` itself.
7. Agent models: (a), game master on the session model, NPC `sonnet`, referee `haiku`.
8. `resume_game`: (a), as recommended.
