# Saved example traces

Each file is the JSONL mirror of one game's `spans` table: one span per line, linked by `parent_span_id`, one trace per turn (`<run_id>-t0001`, ...). All were produced by running the real code offline; none was written or edited by hand. Kinds are `agent`, `llm_call`, `tool_call` and `referee`; a rejected NPC draft keeps its raw text in a span with status `rejected`.

| File | What it shows |
| --- | --- |
| `example-demo-game.jsonl` | The README's demo game: a bad exit, a guarded clue won by a polite repeat, a `hint`, and a winning accusation. |
| `example-bot-win.jsonl` | The scripted bot solving the case in 23 turns. |
| `example-bad-exit.jsonl` | `go lighthouse` from the Quay: the `go` span has status `error`, returns the real exits, and changes nothing. |
| `example-refused-reveal-low-trust.jsonl` | Turn 2: Ysolde's NPC model calls `reveal_clue(C5)` at trust 0 and the engine refuses it ("trust too low"); her reply is the fallback line. Turn 3: a polite repeat raises trust and C5 is revealed. |
| `example-referee-leak.jsonl` | Odrin's first draft states C5, which only Ysolde knows. The referee's code check rejects it (`leaked: ["C5"]`), the draft is regenerated with the referee's note, and the second draft passes. |
| `example-fallback-line.jsonl` | The same leak on all three drafts: three `rejected` agent and referee spans, then Odrin's fixed fallback line (`fallback: true` on `talk`). |
| `example-referee-model-check.jsonl` | Mira's draft adds a stray fact no clue keyword catches. The code check passes, the referee's model check answers `LEAK`, and the regenerated draft passes. |
| `example-stuck-nudge.jsonl` | Eight turns with no new clue: `end_turn` on turn 9 arms the nudge (`nudge_armed: true`), and an off-topic "Cold morning" to Odrin on turn 11 delivers C2. |
| `example-wrong-accusation.jsonl` | An accusation citing a clue not in the notebook is refused and the game goes on; accusing Hale then loses, and the ending shows the true timeline. |

## How they were made

The demo game is the trace `harbor play` wrote while producing the README transcript, renamed from its timestamped run id:

```
.venv/bin/harbor play < docs/demo-commands.txt
mv traces/<run_id>.jsonl traces/example-demo-game.jsonl
```

All the others come from one script, which plays each game in memory through `Session` and writes `traces/example-<name>.jsonl`:

```
.venv/bin/python traces/make_examples.py
```

The offline stand-ins never misbehave, so for the bad examples the script swaps in a `ScriptedLLM` that does: an NPC model that states a clue it does not know, one that adds a stray fact, one that tries to reveal a guarded clue, and a referee model that answers `LEAK` once. The engine, tools, referee and tracing that handle them are the real code. Timestamps differ between runs; everything else is deterministic.

Per-run traces written by `harbor play` (`traces/<run_id>.jsonl`) are ignored by git. These examples were added with `git add -f`.
