"""The referee: does an NPC's draft reply state facts that character could not know?

Two checks, cheapest first:
1. Code check. A reply that matches a clue's keywords is a leak unless that
   clue went through `reveal_clue` by this character in this conversation, is
   in the notebook from this character, or was named by the player in the
   message being answered (ADR 001 #4, ADR 002). The character's own pointer
   lines are removed first, and a keyword that also occurs in the player's
   message or in the character's persona and speech style does not count.
2. Model check, only when the code check passes: a cheap model call that sees
   the facts this character may use and the reply, and answers OK or LEAK.
   If the check model fails, the draft is treated as a leak (fails closed).

The referee reads game state but never changes it. `talk` decides what to do
with a rejection (regenerate up to 2 times, then the fallback line).
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field

from harbor.engine import rules, state
from harbor.engine.state import Game

from harbor.engine.interaction import LEAK_NOTE, CHECK_FAILED_NOTE


@dataclass
class Verdict:
    ok: bool
    leaked: list[str] = field(default_factory=list)   # clue ids, from the code check
    reason: str = ""
    error: bool = False                                # the model check failed


def allowed_clues(game: Game, character_id: str, message: str) -> set[str]:
    """Clue ids this character may mention in the reply being checked."""
    since = state.events_of(game, "clue_found", since_last="talk_started") + \
        state.events_of(game, "clue_confirmed", since_last="talk_started")
    allowed = {e["clue_id"] for e in since if e["source_character"] == character_id}
    allowed |= {r["clue_id"] for r in state.notebook(game) if r["source_character"] == character_id}
    allowed |= rules.shown_clues(game, message)
    return allowed


def _strip_own_lines(game: Game, character_id: str, reply: str) -> str:
    """Remove this character's own pointer lines (chosen by the engine) from the reply."""
    for row in game.conn.execute("SELECT line FROM pointers WHERE character_id = ?",
                                 (character_id,)):
        reply = re.sub(re.escape(row["line"]), " ", reply, flags=re.IGNORECASE)
    return reply


def code_check(game: Game, character_id: str, message: str, reply: str) -> Verdict:
    """Keyword check. A keyword does not count when it is in the player's message (an
    echo adds no fact) or in this character's own persona and speech style."""
    allowed = allowed_clues(game, character_id, message)
    own = game.conn.execute("SELECT persona, speech_style FROM characters WHERE id = ?",
                            (character_id,)).fetchone()
    harmless = f"{message} {own['persona']} {own['speech_style']}"
    text = _strip_own_lines(game, character_id, reply)
    leaked = []
    for clue in game.conn.execute("SELECT id, keywords FROM clues ORDER BY rowid"):
        if clue["id"] in allowed:
            continue
        said = [k for k in rules.matches(text, json.loads(clue["keywords"]))
                if not rules.matches(harmless, [k])]
        if said:
            leaked.append(clue["id"])
    if leaked:
        return Verdict(False, leaked, "reply matches clues not revealed by this character: "
                       + ", ".join(leaked))
    return Verdict(True)


def model_check_input(game: Game, card: dict, message: str, reply: str) -> str:
    allowed = allowed_clues(game, card["id"], message)
    facts = [r["text"] for r in rules.knowledge(game, card["id"]) if r["clue_id"] in allowed]
    pointers = [r["line"] for r in game.conn.execute(
        "SELECT line FROM pointers WHERE character_id = ? ORDER BY rowid", (card["id"],))]
    stated = [rules.clue(game, c)["text"] for c in sorted(rules.shown_clues(game, message))]
    return json.dumps({"character": f"{card['name']}, the {card['role']}",
                       "allowed_facts": facts, "may_send_the_player_to": pointers,
                       "player_said": message, "facts_the_player_stated": stated,
                       "reply": reply}, indent=1)


def parse_verdict(value) -> str:
    """Accept only an entire, case-sensitive verdict; everything else vetoes."""
    if isinstance(value, str) and value.strip() in ("OK", "LEAK"):
        return value.strip()
    return "INVALID"


def referee_context(game: Game, card: dict, message: str, reply: str) -> dict:
    """The referee's permitted facts, excluding every other character's knowledge."""
    return json.loads(model_check_input(game, card, message, reply))
