# Harbor Mystery — M7 Architecture & Implementation Brief

**Status:** Proposed architecture  
**Target milestone:** M7 — Claude Code plugin and multi-agent orchestration  
**Primary reference:** `docs/BRIEF.md`  
**Related decision:** `docs/adr/003-drop-hosted-mode.md`

---

## 1. Purpose and project direction

Harbor Mystery is a detective game built around a multi-agent architecture.

The game is the primary product. The agent architecture is an important engineering differentiator, but we should not sacrifice the player experience or introduce unnecessary infrastructure simply to demonstrate technical complexity.

Our objectives are:

1. Build an enjoyable detective game with believable NPC interactions.
2. Use multiple AI agents with distinct responsibilities and restricted knowledge.
3. Ensure that the game engine, rather than model prompts, enforces game rules and state transitions.
4. Make agent behaviour reliable, recoverable and testable.
5. Preserve the ability to trace and understand agent execution.
6. Keep the architecture extensible without prematurely building a generic agent framework.

The project should demonstrate practical AI engineering: agent orchestration, tool use, context isolation, state management, validation, evaluation and observability.

We are not attempting to build a general-purpose agent framework or observability platform.

### Product-first engineering principle

Whenever an architectural decision creates a trade-off between product complexity and engineering sophistication, prefer the simplest architecture that delivers the intended product behaviour reliably.

Do not introduce abstractions solely to demonstrate a particular design pattern.

---

## 2. Why the architecture is changing

The original architecture used Python to own the complete agent execution lifecycle.

Python managed:
- Game master agent execution.
- NPC agent execution.
- Referee model checks.
- Model/tool interaction loops.
- Tool-call limits and retry handling.

ADR 003 deliberately removed the hosted model integration. Real model execution will instead happen through Claude Code and its subagent capabilities.

This changes the ownership of agent orchestration.

The original design assumed that a Python tool could synchronously invoke another agent. This assumption no longer holds when the agents are executed by Claude Code.

The MCP server exposes tools to Claude Code; it does not become a general-purpose mechanism for spawning Claude Code subagents.

Trying to preserve the old synchronous `talk()` implementation would therefore create an architectural mismatch.

It would also introduce undesirable coupling between the Python server and the Claude Code execution environment.

### New architectural decision

**Claude Code owns agent orchestration. Python owns the authoritative game runtime.**

This is a change in execution ownership, not a replacement of the game engine.

We want to preserve the strongest properties of the existing implementation:
- Deterministic game state.
- Engine-enforced rules.
- Restricted NPC tools.
- Knowledge isolation.
- Validation and rejection of unsafe responses.
- Transactional state changes.
- Event sourcing and replay.
- Structured tracing.

The implementation must preserve these properties even though the model execution now happens outside Python.

---

## 3. Responsibility boundaries

### Claude Code — agent orchestration

Claude Code is responsible for:
- Executing the game master agent.
- Invoking NPC subagents.
- Invoking the referee subagent.
- Managing the sequence of agent interactions.
- Passing explicitly permitted context between agents.
- Handling retry decisions.
- Producing the final player-facing narration.

The game master is the primary coordinator.

It decides which game operation to perform and coordinates the NPC/referee workflow when a conversation is requested.

### Python MCP server — application runtime

Python remains responsible for:
- The authoritative game state.
- SQLite persistence.
- Event sourcing and replay.
- Game rules and time progression.
- Tool registration and argument validation.
- Tool authorization.
- Character knowledge boundaries.
- Trust and clue validation.
- Pending interaction lifecycle.
- Staging, committing and rejecting state changes.
- Deterministic referee checks.
- Fallback responses.
- Execution tracing and correlation.

The Python runtime must never trust a model-generated decision without validating it.

### Agent responsibilities

**Game Master**
- Understand player intent.
- Choose appropriate game tools.
- Coordinate NPC interactions.
- Decide when to request hints or investigations.
- Narrate only information available through authorized game results.
- Never directly modify game state.

**NPC**
- Represent one character.
- Maintain that character's personality and speech style.
- Use restricted tools to retrieve knowledge and interact with the game.
- Produce a conversational draft.
- Never access another character's knowledge or the complete case solution.

**Referee**
- Evaluate an NPC draft for unauthorized information disclosure.
- Receive only the information necessary for evaluation.
- Return a structured verdict.
- Never modify game state or directly finalize an interaction.

The referee's semantic assessment is probabilistic. It supplements, but never replaces, deterministic engine-side authorization and validation.

---

## 4. Target architecture

Conceptually:

```text
Claude Code
│
├── Game Master Agent
│   │
│   ├── General game tools
│   ├── Starts NPC interactions
│   ├── Invokes NPC subagents
│   ├── Invokes referee subagent
│   └── Narrates validated results
│
├── NPC Subagent
│   │
│   ├── Restricted character context
│   └── Interaction-scoped NPC tools
│
└── Referee Subagent
    │
    └── Restricted evaluation context

          MCP boundary
               │
               ▼

Python MCP Server
│
├── GM tool interface
├── NPC tool interface
├── Interaction lifecycle
├── Authorization and validation
├── Deterministic referee checks
├── Staged mutations
├── Finalization
└── Tracing
     │
     ▼
Game Engine
│
├── Rules
├── State
├── Event log
├── SQLite
├── Replay
└── Scoring
```

This is a logical architecture. The actual Claude Code plugin and subagent configuration must be designed around the capabilities and restrictions of the current Claude Code plugin system.

Do not assume that an MCP server can invoke subagents or that subagents automatically inherit every parent tool and permission.

Verify the actual tool availability and execution behaviour before implementing the integration.

---

## 5. NPC interaction lifecycle

This is the most important architectural change in M7.

The existing implementation uses a synchronous `talk()` operation that starts an NPC agent, executes its tools, runs the referee and returns an accepted response.

That model must change because Python no longer executes the NPC or referee models.

Introduce an explicit interaction lifecycle.

### Proposed lifecycle

```text
PENDING_NPC
    │
    ▼
PENDING_REFEREE
    │
    ├── ACCEPTED
    │     └── COMMITTED
    │
    └── REJECTED
          │
          ├── Retry NPC
          │
          └── FALLBACK
```

### Step 1: Begin interaction

The game master invokes a tool such as:

`begin_talk(character, message)`

The server:
- Validates character existence and presence.
- Advances the game clock according to existing rules.
- Records the player's message.
- Establishes a server-generated interaction ID.
- Creates the pending interaction.
- Returns only the NPC's authorized context.

The interaction ID is generated and owned by the server.

The NPC must not be able to choose its character identity or obtain another interaction's context.

### Step 2: NPC execution

The game master invokes the appropriate NPC subagent.

The NPC receives:
- Character identity.
- Persona and speech style.
- Player message.
- Interaction ID.
- Authorized instructions and tools.

It does not receive:
- The full case file.
- The complete knowledge of other NPCs.
- The solution.
- Unrestricted GM tools.

NPC tool calls are authorized against the server-side interaction record.

The server resolves the character from the interaction ID, not from a model-supplied character ID.

### Step 3: Submit NPC draft

The NPC returns a draft through an explicit submission operation.

The server stores the draft and transitions the interaction to `PENDING_REFEREE`.

The draft is not yet player-visible.

### Step 4: Referee execution

The game master invokes the referee subagent.

The referee receives:
- The NPC draft.
- Player message.
- Character identity.
- Relevant authorized facts and their provenance.
- The information necessary to distinguish permitted disclosures from leaks.

It returns a structured verdict.

Do not allow free-form verdict parsing such as accepting arbitrary text that does not contain a rejection keyword.

The server validates the verdict schema and the interaction state.

### Step 5: Finalization

The game master submits the referee verdict through a finalization tool.

The Python server:
- Verifies the interaction is awaiting a verdict.
- Verifies the draft and verdict belong to the same interaction and attempt.
- Runs deterministic validation.
- Commits staged actions if accepted.
- Discards staged actions if rejected.
- Records the final outcome.
- Returns the accepted response or retry/fallback instruction.

The server must never trust the game master's claim that a response was accepted without checking the authoritative interaction state.

### Step 6: Retry and fallback

Preserve the original maximum of three NPC attempts: one initial attempt and up to two regenerations.

A rejected attempt must not leave committed game-state mutations.

The retry receives appropriate rejection feedback without receiving unrestricted referee or case information.

After the final rejection, the engine supplies the predefined in-character fallback response.

The fallback must not require another model call.

---

## 6. Transaction and state management

The original implementation uses SQLite savepoints around tool execution and NPC draft attempts.

Previously, the Python process could keep a savepoint open while executing the NPC and referee model calls.

This is no longer a suitable design because Claude Code controls those calls and their duration.

Do not keep a SQLite transaction or savepoint open while waiting for Claude Code.

### New approach: staged mutations

Introduce a durable pending-interaction representation.

Conceptually:

```python
PendingInteraction(
    interaction_id="...",
    character_id="ysolde",
    player_message="...",
    attempt=1,
    status="pending_npc",
    staged_actions=[],
    draft=None,
    created_at="...",
)
```

This is illustrative, not a mandatory schema.

NPC tools should stage proposed mutations instead of immediately changing committed game state.

On acceptance:
- Validate staged actions.
- Apply them within a short database transaction.
- Append corresponding events.
- Mark the interaction committed.

On rejection:
- Discard the staged actions.
- Record the rejection.
- Preserve the rejected draft and associated trace information.
- Start a clean retry attempt.

On process interruption:
- Pending interactions must be identifiable.
- The server must not silently treat incomplete interactions as accepted.
- Recovery or safe abandonment must be deterministic.

Ensure that repeated finalization requests cannot apply the same mutations twice.

Use interaction and attempt identifiers to enforce idempotency.

### Important constraint

The engine remains the only authority that writes game state.

The model proposes actions. The server validates and decides.

---

## 7. Interaction-scoped authorization

The current active-character mechanism is a core security property.

However, a single global `active_character` is no longer an ideal abstraction when agent execution is controlled externally.

Introduce interaction-scoped identity.

Conceptually:

```text
interaction_id -> character_id
```

Every NPC operation resolves its character through this mapping.

Validate:
- Interaction existence.
- Interaction ownership and lifecycle status.
- Character identity.
- Permitted operation.
- Attempt identity where relevant.
- Whether the interaction has already been finalized.

Do not trust an interaction ID alone as sufficient authorization.

The server must ensure that a tool call is associated with the correct execution context and that a stale or finalized interaction cannot be reused.

If the MCP transport exposes distinct caller/session identities, use them where appropriate. Otherwise, establish an equally strong server-controlled binding between the Claude execution and its interaction capability.

The GM must not gain NPC permissions merely by knowing an interaction ID.

Tool audiences and permissions must be enforced by Python, not just by agent definitions or prompts.

---

## 8. Reliability and trust requirements

Reliability is a product requirement, not merely an infrastructure feature.

We want the following properties:

1. NPCs cannot retrieve facts outside their authorization.
2. The GM cannot directly access the case solution.
3. Rejected NPC attempts cannot mutate committed game state.
4. Every player-visible NPC response passes server-side validation.
5. Malformed or missing referee verdicts cannot be interpreted as acceptance.
6. Model/subagent failures have deterministic fallback behaviour.
7. Repeated requests cannot duplicate committed mutations.
8. Stale interactions cannot be finalized.
9. Interrupted interactions can be recovered or safely abandoned.
10. Existing deterministic game behaviour remains testable without Claude Code.

Do not claim that semantic information leakage is completely prevented by a referee model.

The enforceable guarantee is that tools restrict access to unauthorized facts. The referee provides an additional semantic check on generated responses.

Keep these guarantees distinct in documentation and tests.

---

## 9. Observability and tracing

Tracing is an existing strength of Harbor Mystery and must be preserved.

M7 introduces a distributed execution boundary between Claude Code and Python.

We want to reconstruct an entire player interaction even though it spans multiple agent executions and an MCP server.

Introduce or preserve the following correlation identifiers:

- `run_id`
- `turn_id`
- `interaction_id`
- `attempt_id`
- `span_id`
- `parent_span_id`

Conceptually:

```text
run
 └── turn
      └── interaction
           ├── GM execution
           ├── NPC execution
           │    ├── NPC tool calls
           │    └── NPC draft
           ├── Referee execution
           └── Finalization
                ├── Deterministic validation
                └── Committed events
```

Trace important lifecycle transitions:
- Interaction creation.
- NPC execution start/end.
- NPC tool calls.
- Draft submission.
- Referee execution.
- Referee verdict.
- Retry decisions.
- Finalization.
- State mutations.
- Fallback and failure paths.

Where Claude Code execution metadata is available, correlate it with the Python trace.

Do not invent or fabricate model token counts, costs or timing data if the execution environment does not expose them.

Keep the current SQLite and JSONL tracing model operational.

Avoid building a full observability dashboard as part of M7.

The objective is to preserve the information required for future cross-agent execution analysis.

---

## 10. Evaluation strategy

We need to demonstrate that the system works with real agents, not just that the deterministic engine tests pass.

Extend the existing test suite with targeted evaluation scenarios.

### Engine and lifecycle tests

- Valid interaction lifecycle.
- Invalid interaction ID.
- Wrong character/tool association.
- Duplicate draft submission.
- Duplicate finalization.
- Finalization with stale verdict.
- Rejected attempt rollback.
- Retry limit.
- NPC failure.
- Referee failure.
- Server interruption with pending interaction.
- Recovery of pending interactions.

### Knowledge isolation tests

- NPC attempts to retrieve another character's facts.
- NPC requests an unknown clue.
- NPC submits a draft containing an unauthorized fact.
- NPC attempts indirect disclosure.
- NPC tries to access the solution.
- NPC attempts to reuse an old interaction.

### Real-agent evaluations

Use repeatable scenarios to measure:
- Tool selection accuracy.
- Interaction completion rate.
- Unauthorized fact disclosure rate.
- Referee rejection rate.
- False rejection rate.
- Retry success rate.
- Average interaction latency.
- Model usage and cost, where available.

Keep real-model evaluations separate from deterministic unit tests.

Store representative traces for successful interactions, rejected drafts, retries, failures and fallback responses.

Do not make live-model evaluations a prerequisite for running the ordinary offline test suite.

---

## 11. Product scope and engineering scope

Harbor Mystery remains a detective game.

M7 should enable real model-powered NPC conversations while preserving the existing game mechanics.

Do not use M7 as an opportunity to introduce:
- A generic agent framework.
- A generic workflow engine.
- A custom model provider abstraction.
- Multi-model routing.
- A custom vector database.
- A distributed agent scheduler.
- A complete observability platform.
- Unnecessary concurrent agent execution.

The project should remain small enough to understand and maintain.

Future improvements should be documented in `docs/IDEAS.md`.

### Definition of success

M7 is successful when:

- The Claude Code plugin installs and starts correctly.
- `/harbor-mystery` starts a playable game.
- The GM can complete player turns using real Claude Code execution.
- NPC and referee subagents participate in conversations.
- The interaction lifecycle is enforced by Python.
- NPC knowledge isolation is enforced server-side.
- Rejected drafts cannot leave committed mutations.
- Retry and fallback behaviour work.
- The game can complete an accusation.
- Traces correlate the full interaction across agents and tools.
- Existing offline tests continue passing.

The project must remain independently testable without Claude Code.

---

## 12. Implementation approach

Do not immediately start writing plugin files or changing the existing `talk()` implementation.

First inspect the actual repository and map the current execution flow.

### Phase 0 — Architecture validation

Inspect:
- `docs/BRIEF.md`
- `docs/STRUCTURE.md`
- `docs/adr/003-drop-hosted-mode.md`
- `CLAUDE.md`
- `src/harbor/tools/gm_tools.py`
- `src/harbor/tools/npc_tools.py`
- `src/harbor/tools/registry.py`
- `src/harbor/referee.py`
- `src/harbor/session.py`
- `src/harbor/tracing.py`
- Existing engine schema, events and transaction handling.
- Existing agent and integration tests.

Identify which existing components can be reused and which require changes.

Verify the current Claude Code plugin, MCP and subagent capabilities against the installed CLI and official documentation.

Do not assume that subagents automatically have access to the same MCP tools as the parent.

Produce a concise implementation design covering:
- Module boundaries.
- MCP tool contracts.
- Interaction state machine.
- Pending interaction persistence.
- Authorization.
- Staged mutation handling.
- Retry/fallback behaviour.
- Cross-agent tracing.
- Test strategy.

Document the architecture in a new ADR or update the relevant architecture records.

### Phase 1 — Interaction lifecycle

Implement the Python-side lifecycle independently of Claude Code.

Create tests for:
- Beginning an interaction.
- NPC tool authorization.
- Draft submission.
- Referee verdict submission.
- Acceptance and rejection.
- Staged mutation commit/rollback.
- Retry handling.
- Fallback.
- Interrupted interactions.
- Idempotent finalization.

Use scripted agents or direct test fixtures.

Do not require a real model for these tests.

### Phase 2 — MCP integration

Expose the appropriate GM, NPC and finalization tools through MCP.

Keep the existing engine and domain logic authoritative.

Avoid duplicating business rules inside MCP handlers.

Verify that each tool's schema, permissions and error behaviour match the existing registry conventions.

### Phase 3 — Claude Code plugin

Implement:
- Plugin manifest.
- Marketplace metadata.
- `/harbor-mystery` command.
- Game master agent instructions.
- NPC subagent definition.
- Referee subagent definition.

Make sure the agent instructions explain:
- Who owns orchestration.
- Which tools each agent may use.
- What context each agent is allowed to receive.
- The NPC interaction lifecycle.
- How retries and fallback work.
- That Python is authoritative for all state changes.

### Phase 4 — End-to-end validation

Run the complete offline test suite.

Validate the plugin.

Run real-agent scenarios.

Inspect actual traces.

Test failure and recovery paths, not just a successful conversation.

Play a complete game through accusation.

### Phase 5 — Documentation and release

Update:
- `README.md`
- `CLAUDE.md`
- `docs/STRUCTURE.md`
- Relevant ADRs
- Trace documentation

Include an example of a complete NPC interaction and its corresponding execution trace.

Keep M7's scope focused on the architecture and playable product.

---

## 13. Working agreements

- Read this document and the existing project specification fully.
- Treat existing tests as architectural constraints unless a test explicitly needs to change because of this approved architecture.
- Preserve the existing offline experience.
- Do not remove working features merely to simplify M7.
- Do not weaken existing knowledge-isolation guarantees.
- Do not introduce unnecessary dependencies.
- Keep Python as the authoritative state owner.
- Document meaningful architectural changes through ADRs.
- Run tests after each implementation phase.
- Do not silently change product scope.
- If the current architecture makes an acceptance criterion impossible, explain the conflict and propose alternatives before implementing a workaround.

At each phase, report:
1. What changed.
2. What works.
3. Tests run and their results.
4. Architectural deviations.
5. Remaining risks.
6. What comes next.

The goal is not merely to get a Claude Code plugin running.

The goal is to build a playable product with a well-defined, reliable multi-agent architecture that can be understood, tested, extended and observed.


# M7 Architecture Brief — Amendments from M6 Review

These amendments supplement the existing M7 architecture brief. They do not change its core direction: Claude Code owns agent execution and orchestration; the Python engine remains the authoritative owner of game state, rules, tools, and persistence.

The purpose is to carry forward a few lessons from M6 before implementation begins.

## 1. Referee verdicts must have a strict contract

M6's referee currently treats any model response that does not begin with `LEAK` as an acceptance. This means malformed, ambiguous, or unexpected responses can accidentally approve a draft.

M7 must use a strict structured verdict contract.

**Required behavior:**

- `OK` means the draft is accepted.
- `LEAK` means the draft is rejected.
- Any other response—including empty output, malformed output, or an unexpected value—is an invalid verdict.
- Invalid verdicts must fail closed: they must never be interpreted as acceptance.
- Model invocation failures and invalid verdicts must be distinguishable in traces.
- The GM may retry a rejected or invalid draft within the existing retry budget. Exhausting that budget leads to the existing safe fallback behavior.

Prefer a structured response schema over free-form text if the Claude Code agent interface supports reliable structured output. The Python engine must still validate the result independently.

The referee's job is to evaluate information disclosure, not to authorize arbitrary game-state mutations.

## 2. Cross-agent trace correlation is an M7 requirement

M6's tracing model works well for synchronous, in-process execution. M7 changes that assumption: GM, NPC, and referee work may happen in separate Claude Code agent invocations, with MCP calls occurring between them.

A process-local span stack is not sufficient to represent this lifecycle.

Introduce explicit correlation identifiers:

- `run_id`: the game run.
- `turn_id`: the player turn.
- `interaction_id`: one NPC conversation lifecycle.
- `attempt_id`: one NPC draft and referee evaluation attempt.
- `span_id`: one operation.
- `parent_span_id`: the operation that initiated the current operation.

These identifiers must be passed explicitly across agent and MCP boundaries. Do not depend on shared process memory or implicit context propagation.

At minimum, traces should make it possible to reconstruct:

1. The GM's decision to initiate an interaction.
2. The NPC draft request and response.
3. NPC tool calls and their results.
4. The referee evaluation and verdict.
5. Any retry and the reason for it.
6. The final accepted response or fallback.
7. The game-state mutations that were committed.

Rejected attempts must remain observable even though their proposed game-state changes are discarded.

Do not fabricate token usage, model costs, or timing information when the underlying agent interface does not provide it. Record unavailable metadata as unavailable.

### Trace persistence semantics

M7 must explicitly define what happens when trace persistence fails.

Game-state correctness must not depend on trace storage succeeding. At the same time, trace failures must not silently disappear.

Define and document:

- Which trace events are buffered and when they are persisted.
- What happens when SQLite or JSONL persistence fails.
- How persistence failures are surfaced to the operator.
- Whether a run can continue after a trace persistence failure.
- Which trace information, if any, can be recovered after a process interruption.

Do not claim that a trace is durable until its persistence has succeeded.

## 3. Authorization must be enforced at the MCP boundary

M6's internal tool registry accepts an `audience` argument. That is reasonable inside the current Python execution model, but it must not become a caller-controlled authorization mechanism when tools are exposed through MCP.

M7 must enforce the following:

- The caller must not be able to choose its own audience or permissions.
- The server determines which operations are permitted for a given interaction.
- Every operation is checked against server-owned interaction state.
- An interaction must be active and in the correct lifecycle state before its tools can be used.
- Tool calls must be scoped to the relevant interaction and attempt.
- Finalization must be idempotent, so repeated submissions cannot commit the same interaction twice.

Do not treat a caller-provided `interaction_id` as proof of authorization. It is a lookup key, not an authorization credential.

### Referee verdict provenance

The architecture must also address how the engine knows that a verdict actually came from the referee.

If the GM, NPC, and referee agents share the same MCP connection and permissions, the engine cannot cryptographically or structurally distinguish a genuine referee verdict from a value submitted by another agent.

Do not describe such a setup as having strong agent-level authorization.

Instead:

- Investigate whether Claude Code's subagent execution model provides a reliable way to isolate agent identities or tool permissions.
- If it does, document and enforce that boundary.
- If it does not, treat the referee verdict as an advisory model output rather than a trusted authorization decision.
- Keep deterministic Python-side validation as the actual enforcement boundary for game-state invariants and information access.

The engine must never rely exclusively on an agent's claim that an action or response is safe.

## 4. Use durable interaction state instead of long-lived SQLite savepoints

M6's savepoint-based rollback model is appropriate for synchronous in-process model calls. M7 introduces external agent handoffs, which may take substantially longer and may be interrupted.

Do not keep a SQLite transaction or savepoint open while waiting for Claude Code agents.

Use a durable interaction lifecycle instead.

Suggested lifecycle:

`PENDING → DRAFTING → EVALUATING → COMMITTED`

Rejected attempts should return to a state that permits another attempt, subject to the retry budget. Terminal failure or exhaustion should lead to a safe fallback and a completed interaction.

The implementation should:

1. Create a pending interaction and persist its identity.
2. Associate each draft attempt with that interaction.
3. Stage proposed mutations without applying them to authoritative game state.
4. Evaluate the draft.
5. Validate the proposed mutations independently in Python.
6. Apply accepted mutations in a short database transaction.
7. Record committed events and mark the interaction finalized.
8. Discard staged mutations for rejected or abandoned attempts.

The implementation must also define recovery behavior for interrupted interactions. A process restart must not leave an ambiguous interaction that can accidentally commit twice or continue using stale authorization.

Keep the existing retry policy unless implementation reveals a concrete reason to change it: three attempts total (initial attempt plus up to two regenerations), followed by the safe fallback.

Do not introduce a generic workflow engine to implement this lifecycle. A small, explicit state machine owned by the game engine is sufficient.

## 5. Add adversarial knowledge-isolation validation

M6's keyword-based code check and model-based referee check are useful layers, but neither should be treated as proof that information leakage is impossible.

M7 should include adversarial scenarios in its validation plan, especially:

- A character indirectly reveals a clue without using its exact keywords.
- A character paraphrases information it should not know.
- A character combines individually harmless facts to reveal a restricted conclusion.
- The player asks leading questions that attempt to extract another character's private knowledge.
- A character repeats information from its persona or speech style that resembles a restricted clue.
- A rejected draft attempts to mutate game state before being rejected.
- An invalid referee response attempts to cause accidental acceptance.

The objective is not to build a perfect natural-language leakage detector. It is to establish clear boundaries, test known failure modes, and ensure that a rejected response cannot cause unauthorized state changes.

Evaluation should distinguish between:

- Deterministic rule violations.
- Model-detected information leakage.
- Invalid or unavailable referee evaluations.
- False positives that unnecessarily prevent normal gameplay.

Preserve the existing offline `ScriptedLLM` testing approach so these scenarios can be exercised deterministically.

## 6. Keep the M7 scope product-first

These amendments are not a request to turn Harbor Mystery into a general-purpose multi-agent framework.

The implementation should continue to prioritize a playable detective game and use the architecture to demonstrate practical agent engineering.

In particular, do not add:

- A generic workflow orchestration framework.
- A custom model-provider abstraction without a concrete need.
- A general-purpose authorization framework.
- A separate observability platform.
- Distributed scheduling or concurrency infrastructure.
- Vector databases or retrieval infrastructure unrelated to the current gameplay requirements.

The architecture should be as simple as possible while making the important boundaries explicit.

## 7. Update the M7 implementation plan

Add the following checks to the existing phases:

**Phase 0 — Architecture validation**
- Confirm Claude Code's actual MCP and subagent execution semantics.
- Determine whether agent identities and permissions can be isolated.
- Define the strict referee verdict contract.
- Define interaction and attempt state transitions.
- Define cross-agent trace correlation and persistence semantics.
- Present the proposed contracts and architecture before implementation.

**Phase 1 — Engine lifecycle**
- Implement durable pending interactions and staged mutations.
- Remove the need for long-lived SQLite savepoints across agent handoffs.
- Enforce server-owned interaction authorization.
- Make finalization idempotent.
- Validate strict referee verdict parsing.

**Phase 2 — MCP and agent integration**
- Pass explicit correlation identifiers across agent boundaries.
- Ensure MCP callers cannot choose their own audience.
- Implement the NPC, GM, and referee handoff using the confirmed Claude Code capabilities.
- Preserve the existing offline execution path.

**Phase 3 — Validation and observability**
- Verify that rejected attempts cannot commit mutations.
- Verify malformed referee responses fail closed.
- Exercise adversarial knowledge-leakage scenarios.
- Verify that a complete interaction can be reconstructed from its trace.
- Exercise interrupted interactions and trace persistence failures.

**Phase 4 — Gameplay validation**
- Verify that the game remains enjoyable and solvable without a walkthrough.
- Evaluate NPC differentiation, hint quality, plausible wrong theories, and whether the ending feels earned.
- Keep gameplay quality as the final measure of success, not architectural complexity.

---

**Working principle:** Claude Code coordinates agents; Python owns truth. Agent outputs are proposals, not authority. Every state change must pass through an explicit, server-enforced lifecycle, and every important step must be traceable across agent boundaries.
