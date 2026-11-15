# ADR 005: The project ships three cases

Date: 2026-11-15. Status: proposed.

## Context

The brief ships one case, Saltmere, and its scope guard rules out generating cases from a premise. The engine, tools, agents and referee already read everything from the case file, so a second case costs only data. The owner asked for two more, hand-written, to show that nothing in the architecture is Saltmere-shaped.

## Decision

`cases/` holds three hand-written cases, chosen to differ from each other in setting, difficulty, structure and size:

| Case | Setting | Size | Difficulty | Shape |
| --- | --- | --- | --- | --- |
| `saltmere` | Fishing village, harbor sabotage | 6 places, 6 people, 9 clues | Medium | Pointer chain, one red herring |
| `gaiety` | Provincial theatre on opening day | 4 places, 5 people, 8 clues | Easy | The culprit's false alibi (C3) is broken by a witness (C4); hints lead there first, though motive and method alone (C2 + C5) can also win |
| `kestrel` | Snowbound mountain observatory | 8 places, 7 people, 14 clues | Hard | Two red-herring suspects, each with an alibi; evidence split between places (C2, the nailed prints, only `look` finds) and people; five guarded knowledge rows; a win needs 3 supporting clues |

`harbor play --case` and `harbor bot --case` already choose the file; Saltmere stays the default. No engine code changed.

## What "a valid case" means

`tests/test_all_cases.py` runs these checks on every `cases/*.json`:

1. It loads, its file name is its `case_id`, and `harbor check-case` passes.
2. The solver (ADR 002 #34) reaches every place and clue, and the evidence points to exactly one person, the solution's culprit. Every required clue implicates the culprit; red herrings implicate someone else; alibis implicate nobody. The solver ignores topics and reads the hand-written `implicates` labels, so it cannot tell whether a sensible question reaches a clue or whether a player could argue for someone else; that takes a read-through by a person.
3. Every nudge-order clue is known by someone, and every pointer names a character who knows its clue.
4. The scripted bot wins in under 40 turns, citing at least `min_supporting` required clues.
5. Referee keywords, for the fixed lines a case supplies: fallback lines and the engine's "Ask <name>." pass the code check; a pointer line, even with its punctuation changed, trips at most the clue it points to; any character stating a clue's exact text unrevealed trips exactly that clue; each clue's keywords occur in its own text; no keyword matches a name; a keyword in the premise, a place description, a persona or a speech style must be listed as intended in the test. None of this predicts a real model's paraphrases; the referee's model check covers those.
6. Roles, personas, speech styles, fallback lines and name parts differ between characters, and names avoid characters the typed commands and the bot's parsing split on.

## Consequences

A new case is checked by adding its file. The bot is generic, so a case must be designed so that one open question, one polite repeat and the pointers can win it. This holds a case's design to the bot's limits (it never returns to someone it has asked, and it accuses whoever three clues name), not the other way round.
