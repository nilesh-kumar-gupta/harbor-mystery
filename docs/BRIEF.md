# Harbor Mystery: build brief for the terminal agent

Nov 14, 2026 · @Nilesh

## Brief

*How to use this doc:* export it as Markdown, save it in the repo as `docs/BRIEF.md`, and tell the terminal agent to read it fully before writing any code. Everything below is addressed to that agent.

You are building **Harbor Mystery** over one weekend (deadline: Sunday 15 November 2026, evening IST). It is a text-based detective game played in a terminal. The player arrives in a fishing village where the harbor's dredging winch was sabotaged overnight. They move between places, question villagers, collect clues in a notebook, and have one in-game day to accuse the right person with a motive and evidence.

The game is a test bed. The real goal is a clean, verifiable **multi-agent architecture**: a game master agent and NPC agents that call tools against a SQLite database, a referee that stops characters revealing facts they could not know, and a trace of every step. Do not build graphics, a web UI, packaging of a local model, or fine-tuning.

**Definition of done**

1. A clean checkout runs with one command and can be played from the first move to an accusation.
2. A scripted bot player solves the case as an automated test, with no human input.
3. In the test set, no NPC reveals a fact outside its knowledge set.
4. Hints work: characters point to who knows a clue, and a stuck player gets a nudge within 8 turns.
5. Every model call and tool call is written as a span to SQLite and a JSONL file.
6. The repo has a README with a demo transcript and is tagged as a release.
7. The same tools are wrapped as an MCP server with a `/harbor-mystery` command and subagents, and install from the repo acting as its own Claude Code marketplace. This comes last.

## Decisions already made

These are settled. Do not reopen them unless one blocks you; if so, say why and propose a change.

- **Language and stack:** Python 3.11+, standard library `sqlite3`, `argparse` for the CLI, `pytest` for tests. Keep dependencies few. No agent frameworks (no LangChain, CrewAI or similar): write the agent loop by hand, because understanding it is the point.
- **Repo layout:** `src/harbor/` with modules `engine`, `agents`, `tools`, `referee`, `tracing`, `llm`, `cli`; `cases/saltmere.json`; `tests/`; `docs/` for this brief and short decision records (`docs/adr/NNN-title.md`, half a page each); `traces/` for saved example traces.
- **Model access:** one `LLMClient` interface with a method that takes a system prompt, messages and tool definitions and returns text plus tool calls. Two implementations: `ScriptedLLM` (deterministic, used in tests) and `HostedLLM` (default: Anthropic Messages API with tool use; model name from the `HARBOR_MODEL` environment variable and key from `ANTHROPIC_API_KEY`). A local-model implementation comes later through the same interface; do not build it now.
- **Tools are plain Python functions** in one registry, each with a JSON schema. The agent loop calls them. An MCP server is a thin wrapper added at the end.
- **The engine enforces the rules, not the prompts.** Game state changes only inside tool calls. The model can propose; the engine validates and decides.
- **Determinism:** a seeded random generator, an append-only event log, and every model reply saved so a run can be replayed.
- **Time model:** the day runs 06:00 to 18:00. `go` and `talk` each use 10 minutes, `look` uses 5. The game ends at 18:00 or on accusation.
- **Scope guard:** no graphics, no web UI, no packaging of a model, no fine-tuning, no case generation from a premise. Only the weekend must-haves unless told otherwise.

## Architecture

One line typed by the player is one **turn**. A turn runs this chain, and every step is a traced span.

1. **Game master agent.** Receives the player's text plus a compact state summary (place, time, notebook titles). It chooses tools: `look`, `go`, `talk`, `notebook`, `hint`, `accuse`. It never writes game facts itself; it narrates what tools return.
2. **NPC agent**, started by the `talk` tool. It is a separate model call loop with its own system prompt (persona, speech style) and its own tools: `recall_knowledge`, `recall_conversation`, `reveal_clue`, `update_trust`. It sees nothing about other characters or about the case file.
3. **Engine validation.** Every tool call is validated in code before it changes anything: the clue must exist, the active character must know it, the trust level must be high enough, and the player must be in the right place.
4. **Referee.** After an NPC reply is produced, a code check compares the clue and fact IDs in the reply against the character's knowledge set, and a second cheap model call checks for stray facts. If the reply leaks, it is rejected and regenerated, up to 2 times, and a fixed in-character fallback line is used after that.
5. **Tick.** Time advances. If no new clue was found in 8 turns, the engine marks the next unrevealed clue as nudgeable and the nearest suitable character brings it up on their own in the next scene. World agents that move offscreen characters are a should-have, built last.
6. **Trace.** Every model call, tool call and referee check is written through one `span()` helper.

**Knowledge isolation rule (critical):** tools never take a character ID as an argument that the model can choose. `talk(character)` sets an *active character* inside the engine, and `recall_knowledge()` and `reveal_clue(clue_id)` act on that active character only. The server enforces this, not the prompt.

**Hint rules:** (a) a character who does not know a clue but knows who does says so in character, for example "ask Mira, she is up all hours"; (b) a stuck-player nudge as described in step 5; (c) trust rises when the player asks politely twice or shows a related clue, which unlocks guarded clues; (d) a `hint` command points toward an unvisited person or place and costs 20 minutes. The model only phrases hints; the engine chooses which clue or person they refer to.

**Accusation:** `accuse(character, motive, clue_ids)` is scored by the engine against the case file. Right culprit with at least 2 correct supporting clues wins; right culprit with fewer is a partial result; wrong culprit loses. The ending reveals the true timeline and which clues were found or missed.

## Data model

One SQLite file per run, loaded from `cases/saltmere.json`. Adjust column details as needed, but keep the separation between case data (read-only) and run data (changes).

| Table | Key columns | Notes |
| --- | --- | --- |
| places | id, name, description | Static case data |
| exits | from\_place, to\_place | Which places connect |
| characters | id, name, role, persona, speech\_style, start\_place, schedule | schedule says where they are at which time |
| clues | id, text, kind, found\_at\_place | kind is motive, method, opportunity or red\_herring; found\_at\_place set means visible on look |
| knowledge | character\_id, clue\_id, unlock\_topic, min\_trust | What each character knows and what unlocks it |
| pointers | character\_id, clue\_id, points\_to\_character | Who a character sends the player to for a clue |
| case\_solution | culprit\_id, motive, method, true\_timeline, required\_clue\_ids | Never exposed to any agent; used only for scoring |
| game\_state | run\_id, clock, place, active\_character, trust, turns\_since\_new\_clue, status | The one mutable row per run |
| notebook | run\_id, clue\_id, source\_character, place, minute | Filled by tools only |
| conversations | run\_id, character\_id, turn, speaker, text | Gives NPCs memory of the day |
| events | run\_id, turn, type, payload | Append-only log of every state change |
| runs | run\_id, seed, case\_id, git\_commit, prompt\_hash, model | One row per game, for replay |
| spans | run\_id, trace\_id, span\_id, parent\_span\_id, kind, name, start\_ts, end\_ts, model, input\_tokens, output\_tokens, input, output, status, attrs | Trace format below; also mirrored to `traces/<run_id>.jsonl` |
| labels | trace\_id, label, note | Created empty for later error analysis |

**Span rules:** every model call, tool call, referee check and agent run goes through one `span()` helper. Save raw model output before validation. Rejected replies are logged with status `rejected`. Use OpenTelemetry GenAI attribute names where they exist (`gen_ai.operation.name`, `gen_ai.agent.name`, `gen_ai.tool.name`, `gen_ai.request.model`, `gen_ai.usage.input_tokens`, `gen_ai.usage.output_tokens`) plus game attributes such as `game.turn`, `game.npc` and `game.clue_id`.

## Seed case: Saltmere

Turn this into `cases/saltmere.json`. You may polish wording and add small details, but keep the structure, the solution and the knowledge assignments, and keep it solvable.

**Premise.** The village of Saltmere wants its silting harbor dredged before the autumn trade ships arrive. Overnight, the dredger's winch cable was cut and its pins knocked loose. The player is a stranger sent to find out who did it. The game starts at 06:00 on the Harbor Quay.

**Places (6) and exits.** Harbor Quay (hub) connects to Harbormaster's Office, Fish Market and Chandlery. Fish Market connects to The Salt Cask (tavern). Chandlery connects to Lighthouse.

**Characters (6).**

- **Odrin Vale**, harbormaster, in the Office. Gruff and tired. Wants the dredging done.
- **Mira Tolle**, net-mender, on the Quay. Warm, talks in half-finished thoughts, up at all hours.
- **Tom Brask**, fishmonger, at the Fish Market. Jovial and evasive. Has a bad knee. **The culprit.**
- **Ysolde Crane**, chandler, at the Chandlery. Precise and guarded about her customers.
- **Bram Oyle**, lighthouse keeper, at the Lighthouse. Quiet, notices boats and lights.
- **Hale Pruitt**, tavern keeper, at The Salt Cask. A gossip. Red herring.

**Solution.** Tom Brask did it. *Motive:* bigger ships would end his monopoly selling to the small boats. *Method:* he cut the cable with bolt cutters bought from Ysolde. *Opportunity:* about 1:30 to 2:30 a.m. by unlit skiff from the fish market dock. A win needs the right culprit plus at least 2 of clues C2, C3, C4, C5 and C6.

| Clue | Kind | Fact | Who knows it, and what unlocks it |
| --- | --- | --- | --- |
| C1 | method | The winch cable was cut clean, and the pins were knocked loose. | Visible on `look` at the Quay; Odrin also knows it |
| C2 | motive | Tom spoke loudly against dredging at last week's town meeting ("bigger ships, smaller catch"). | Odrin (topic: the meeting, objections); Hale |
| C3 | opportunity | A figure with a limp and a lantern walked the quay near 2 a.m. toward the fish market. | Mira (topic: night, lantern, quay) |
| C4 | opportunity | A small unlit skiff left the fish market dock around 1:30 and returned around 2:30. | Bram (topic: lights, water, boats at night) |
| C5 | method | Ysolde sold bolt cutters to Tom two days ago, paid in fish. | Ysolde, needs trust 1 (asked politely twice, or shown C1) |
| C6 | opportunity | Tom left the tavern at midnight, complaining about his knee. | Hale (topic: Tom, midnight, knee) |
| C7 | red herring | Hale argued with Odrin over unpaid harbor fees and said he would "see the harbor sink". | Odrin; Hale admits it only if confronted |
| C8 | alibi | The tavern was lit and noisy until about 3 a.m., and Hale never left the bar. | Mira (topic: tavern, Hale) |

**Pointers (who sends the player where).** Odrin points to Mira ("she's up all hours") and wrongly to Hale. Mira points to Bram ("the lighthouse sees the water"). Bram points to Ysolde ("anyone cutting a cable needed cutters"). Hale points to Odrin for the meeting story. Tom points to nobody and denies everything, claiming he was home asleep. Tom confesses the motive only at trust 2 or when shown C3 and C5.

**True timeline.** 00:00 Tom leaves the tavern. 01:30 he takes the skiff from the fish market dock. About 01:45 he cuts the cable. About 02:00 he walks back along the quay with a lantern. 02:30 the skiff returns. 03:00 the tavern closes.

**Stuck-nudge order:** C2, then C3, then C4, then C5, then C6, skipping clues already found.

## Tools

All tools are plain functions in one registry with a JSON schema each. They return JSON. Errors come back as values (`{"ok": false, "error": "..."}`), never exceptions, so the model can recover. Each call is wrapped in a span.

| Tool | Used by | Arguments | What it does | Time cost |
| --- | --- | --- | --- | --- |
| look | Game master | none | Returns the place description, visible clues, who is present and the clock. Adds visible clues to the notebook. | 5 min |
| go | Game master | place | Checks the exit exists, moves the player, updates where characters are. | 10 min |
| talk | Game master | character, message | Checks the character is present, sets them as the active character, runs the NPC agent loop, returns the validated reply. | 10 min |
| notebook | Game master | none | Lists clues found, with source and place. | 0 |
| hint | Game master | none | The engine picks the next nudge target (a person or place) and returns it. | 20 min |
| accuse | Game master | character, motive, clue\_ids | Scores against `case_solution`, ends the game, returns the result and true timeline. | 0 |
| recall\_knowledge | NPC | none | Returns what the active character knows that the player can unlock now (clue text, pointers), given trust and topic. Never other characters' facts. | 0 |
| recall\_conversation | NPC | none | Returns this character's earlier lines with the player today. | 0 |
| reveal\_clue | NPC | clue\_id | Checks the active character knows it and trust is high enough; adds it to the notebook; returns its text. Invalid IDs return an error. | 0 |
| update\_trust | NPC | reason | Reason is one of polite\_repeat, related\_clue\_shown or rude. The engine decides the change, clamped to one step. | 0 |

**Rules**

1. No tool takes a character ID that the model can choose, except `talk` and `accuse` on the game master side. NPC tools act on the active character only.
2. The game master calls at most 3 tools per turn and each NPC agent at most 4 tool calls per reply, to bound cost and loops. Hitting a limit ends the loop with an in-character fallback line.
3. The NPC reply goes through the referee before it is returned from `talk`.
4. The game master's narration may only use facts that tools returned in this turn.

## Tests and acceptance checks

Write engine tests first and keep them fast and offline by using `ScriptedLLM`. Model-backed checks run separately with `pytest -m live`.

| Check | How | Pass |
| --- | --- | --- |
| Case validity | Load `saltmere.json` and run a solver over the clue graph | Every clue needed for a win is reachable from the start, and the clues point to exactly one culprit |
| Engine rules | Unit tests per tool: bad exits, absent characters, unknown clue IDs, low trust | Each returns an error value and changes no state |
| Knowledge isolation | For each character, scripted replies that try to reveal a clue they do not know | The referee rejects every one, and `reveal_clue` refuses them |
| Determinism and replay | Run a scripted game twice with one seed; replay from the saved log | Same final state hash both times |
| Hints | Bot stalls for 8 turns | A nudge arrives, always a real unfound clue in the stuck-nudge order; pointer hints name a character who really knows it |
| Bot player | A scripted bot follows the pointers and accuses | It wins in under 40 turns with at least 2 correct clues |
| Trace | Read the spans for a 20-turn game | Each turn has agent, tool\_call, llm\_call and referee spans with parents, and no state change occurs outside a tool\_call |
| Live smoke test | Play 10 turns with the hosted model | First words in under 3 seconds for most turns, and the referee leak rate is reported |
| Plugin install | Last step: `claude plugin validate --strict .`, then install from the local marketplace | `/harbor-mystery` starts a game that can be played to an accusation |

## Build order and working agreements

Build in this order. Time boxes are estimates for you, the agent. Commit after each milestone with tests passing.

1. **M0, scaffold (20 min):** repo, `pyproject.toml`, pytest, `src/harbor/` modules, the case loader reading `cases/saltmere.json`, and a first short ADR.
2. **M1, engine core (60–90 min):** schema, state, event log, and the tools `look`, `go` and `notebook`, with unit tests. A plain CLI loop where the human types commands, with no model yet.
3. **M2, tracing and model interface (60 min):** the `span()` helper writing SQLite and JSONL, the `LLMClient` interface, `ScriptedLLM`, and every tool wrapped in a span.
4. **M3, agents (90 min):** the NPC agent loop and the `talk` tool with the active-character rule and trust, the game master agent, and `HostedLLM`. Finish with a live smoke test talking to Mira.
5. **M4, referee and hints (60 min):** the leak check and regeneration, pointer hints, the stuck nudge and the `hint` command.
6. **M5, ending and bot (45 min):** `accuse`, the result screen, the bot player, the case solver, and the determinism and replay test.
7. **M6, release (30–45 min):** README with architecture notes and a real demo transcript, 5–10 saved traces in `traces/` including bad ones, and tag `v0.1.0`. **Checkpoint: the standalone game is now shippable.**
8. **M7, plugin (about 3 hours):** wrap the same tools as an MCP server, add NPC subagent files, a `/harbor-mystery` command, `.claude-plugin/plugin.json` and `marketplace.json`, run `claude plugin validate --strict .`, test an install from the local marketplace, push, and tag `v0.2.0`.

**If behind, cut in this order:** the plugin's polish first, then stretch items (streaming, local-model switch, save and resume, trace export, world agents), then the `hint` command. **Never cut** the referee, the bot-player test or the tracing.

**Working agreements**

- At each milestone, stop and print a short status: what works, what is next, and any change from this brief.
- Do not add features outside this brief. Put ideas in `docs/IDEAS.md` instead.
- Keep prompts in `src/harbor/prompts/*.md` and store a prompt hash in each run record.
- Never put keys in the repo; read them from environment variables.
- Decide small things yourself and note them in a short ADR. Ask the human only about the model setup or a real blocker.
- Write ADRs (half a page each) for: event log versus mutable state, the referee design, the tool registry, and the active-character rule.

## Kickoff prompt

Paste this as the first message in the terminal agent after saving this doc as `docs/BRIEF.md`:

```
Read docs/BRIEF.md in full before doing anything. It is the specification for a weekend project called Harbor Mystery. Follow its decisions, architecture, data model, tool list, tests and build order exactly, and do not add scope. Start with milestones M0 and M1 only: scaffold the repo, load the seed case, build the engine core with look, go and notebook, and write the tests for them. Commit when the tests pass. Then stop and print a short status: what works, what is next, and any place you deviated from the brief. Ask me only about the model setup (which model name to put in HARBOR_MODEL and whether ANTHROPIC_API_KEY is set) when you reach M3.
```
