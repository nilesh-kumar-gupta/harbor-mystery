# Ideas

Out-of-scope ideas go here, not into the code (see docs/BRIEF.md, working agreements).

- Streaming replies from `HostedLLM`, so the first words appear before a whole turn is done (the live smoke test measures whole turns for now).
- A server-side refusal fallback model for `HostedLLM`, once the owner picks `HARBOR_MODEL`.
- An `implicates` field per clue in the case data (owner's data), so the case solver can weigh clues that name nobody, such as C3 (a limp) and C4 (the fish-market dock).
