# ADR 003: Drop hosted mode

Date: 2026-11-15. Status: accepted (owner decision).

## Context

The brief names `HostedLLM` as the default model client: the Anthropic Messages API, with the model name and the API key read from environment variables. It was built in M3 (`llm/hosted.py`, `harbor play --hosted`, the optional `hosted` dependency) with a live smoke test under `pytest -m live`, which was never run. The real model is now planned to arrive in M7 through the Claude Code plugin, where Claude Code itself is the model, so the project never needs an API key.

## Decision

- Remove `HostedLLM` (`src/harbor/llm/hosted.py`), the `--hosted` flag, the `hosted` optional dependency, `tests/test_hosted.py`, `tests/test_live_smoke.py` and the `live` pytest marker (nothing else used it). No model name or API key is read anywhere.
- Keep the `LLMClient` interface and `ScriptedLLM` unchanged; the neutral message format in `llm/base.py` stays as it is.
- `harbor play` stays offline: the game master, the NPCs and the referee's model check are the deterministic `ScriptedLLM` stand-ins in `offline.py`, so a clean checkout plays with one command.
- The hosted-mode parts of ADR 002 rows 15, 22 and 33 are superseded by this ADR.

## Consequences

- There is no live smoke test until M7. The brief's check (first words in under 3 seconds, referee leak rate reported) moves to the plugin.
- The first play with a real model happens through the plugin. Until then every reply the player sees comes from the scripted stand-ins, and the README says so.
- In plugin mode the MCP server cannot make model calls itself. The NPC agent and the referee's model check will have to run as Claude Code subagents, and the server will hand them their inputs and validate what they return through the same tools. M7 has to design that hand-off without weakening the rule that tools, not prompts, enforce knowledge isolation.
