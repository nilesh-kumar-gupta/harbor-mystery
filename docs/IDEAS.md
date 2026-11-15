# Ideas

Out-of-scope ideas go here, not into the code (see docs/BRIEF.md, working agreements).

- Streaming replies from a real model client, so the first words appear before a whole turn is done.
- A `harbor cases` command listing the cases in `cases/` with their size and difficulty, and a case picker in `/harbor-mystery`.
- Character schedules for Kestrel Ridge (Wren going from the darkroom to the dome at noon, Abel taking the mule down at five), once world agents exist.
- A neutral NPC prompt opening: `prompts/npc.md` says "You are a villager", which fits Saltmere but not a theatre or an observatory.
- A bot that returns to people with new evidence, so a case can require a trust-2 clue or a topic only reachable after a later find.
- Publish to Anthropic's plugin directory (claude.ai/directory/manage, paid plan; the repo must be public). Needs, from the pre-submission checklist (claude.com/docs/plugins/pre-submission-checklist): a `LICENSE` or `license` in `plugin.json`; `claude plugin validate --strict` passing, which today fails only on the repo's own `CLAUDE.md` at the plugin root (move the plugin into a subfolder, or move developer notes out of the root); server commands that run a file inside the plugin by its full `${CLAUDE_PLUGIN_ROOT}` path, so a small launcher script replaces `python3 -m harbor.mcp_server` plus `PYTHONPATH`; every file under 256 KiB and at most 512 files (check the example traces); probably an icon. Players also need Python 3.11+ on their machine; say so in the listing, or ship the server in a form that does not need a system Python.
- Making the GitHub repo public would already let anyone install the game with `claude plugin marketplace add nilesh-kumar-gupta/harbor-mystery`, no directory listing needed. Pick a marketplace name less generic than `harbor-mystery-local` first (users cannot have two marketplaces with the same name), and decide whether `plugin.json` pins `version` (users keep the old copy until it is bumped) or tracks commits.
- A version of the game for the regular Claude chat app: chat ignores local MCP servers and plugin agents, so it would need the engine as a remote (https) MCP server and the NPC and referee as something other than subagents.
