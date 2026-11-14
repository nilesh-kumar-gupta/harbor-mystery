# ADR 001: Clarifications to the brief

Date: 2026-11-14. Status: accepted.

## Context

`docs/BRIEF.md` leaves ten points open that affect the schema, the case file or the tools. They were settled before M0 so later milestones do not rework them.

## Decisions

| # | Gap in the brief | Decision |
| --- | --- | --- |
| 1 | Trust is per character, but `game_state.trust` is one column | A `trust(run_id, character_id, level)` table; levels 0 to 2 |
| 2 | Unlocks depend on topic, but `recall_knowledge` takes no arguments | The engine keyword-matches the player's message against `unlock_topics`; an empty list means any message unlocks |
| 3 | "Shown C1" has no mechanism | The engine detects notebook clues referenced in the player's message (by clue keywords); the model does not decide |
| 4 | The referee compares clue IDs, but replies are free text | Each clue has `keywords`; a reply matching a clue must have gone through `reveal_clue` this turn or already be in the notebook from that character |
| 5 | C8 is kind `alibi`, not in the `kind` list | `alibi` is an allowed kind |
| 6 | Tom's confession is not a clue | Added as C9 (motive, Tom, trust 2 or shown C3 and C5); not required for a win |
| 7 | `schedule` exists but nobody moves | The column stays; schedules are empty until world agents (a stretch item) |
| 8 | The stuck counter and the 18:00 timeout change state outside any tool | An engine `end_turn` step runs last in every turn and is recorded as a `tool_call` span |
| 9 | `accuse` takes a motive, but the win rule does not score it | The motive text is recorded and the true motive shown in the ending; it does not affect the result |
| 10 | A stuck nudge could hand over a guarded clue | A nudge never bypasses `min_trust`; a guarded clue is nudged as a pointer to who knows it |

## Case file fields added for these

- `clues[].keywords`: used by decisions 3 and 4.
- `knowledge[].unlock_topics`, `related_clues` (showing one allows a trust step), `unlock_if_shown` (showing all unlocks at any trust).
- `pointers[].line` and `misleading`; `characters[].fallback_line`; `solution.min_supporting`; top-level `nudge_order`.

## Consequences

The case file is the single place to tune topics and keywords. Keyword matching is crude, so the referee's model check stays as the second line of defence.
