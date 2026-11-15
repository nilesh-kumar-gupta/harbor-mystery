"""A scripted bot player. It plays through the game master, as a person would,
and decides only from the narration it is shown:

- `look` everywhere it goes, to learn who is there and where the exits lead;
- ask each new person one open question about last night; ask again politely
  when they hold back, showing the clues already in its notebook;
- follow the people named in replies ("Ask Bram...") before exploring;
- accuse whoever its clues name most often, citing those clues, once someone is
  named by enough of them or when it runs out of leads or turns.

It never reads the database or the case file: it knows exactly what a player knows.
"""

from __future__ import annotations

import re
from collections import deque
from dataclasses import dataclass, field

QUESTION = "What did you notice last night?"
POLITE = "Please, I understand, but it matters for the whole harbor."
ACCUSE_AT = 3                      # clues naming one person before the bot accuses


@dataclass
class BotGame:
    turns: int = 0
    transcript: list[tuple[str, str]] = field(default_factory=list)
    result: str | None = None
    accusation: str | None = None


def player_view(session):
    """What a player gets back from one line: the narration, and whether the game ended."""
    def turn(line: str) -> tuple[str, str, str | None]:
        result = session.turn(line)
        return result.narration, result.status, result.end.get("result")
    return turn


class Bot:
    def __init__(self, session, max_turns: int = 39) -> None:
        self._turn = player_view(session)   # the bot keeps nothing else of the session
        self.max_turns = max_turns
        self.game = BotGame()
        self.place = None
        self.exits: dict[str, set[str]] = {}
        self.people_at: dict[str, str] = {}      # person -> place where last seen
        self.asked: dict[str, int] = {}          # person -> times asked
        self.leads: deque[str] = deque()         # people named in replies
        self.clues: dict[str, str] = {}          # notebook: id -> text
        self.visited: set[str] = set()

    # ---- playing -------------------------------------------------------------

    def say(self, line: str) -> str:
        narration, status, ending = self._turn(line)
        self.game.turns += 1
        self.game.transcript.append((line, narration))
        self._read(line, narration)
        if status == "ended":
            self.game.result = self.game.result or ending
        return narration

    def play(self) -> BotGame:
        self.say("look")
        # one pass can use two turns (ask and ask again, or go and look), and the
        # accusation needs one more: stop with three in hand
        while self.game.result is None and self.game.turns <= self.max_turns - 3:
            if len(self.clues) >= ACCUSE_AT and any(not t for t in self.clues.values()):
                self.say("notebook")                  # learn the texts of new clues
                continue
            if self._ready_to_accuse():
                break
            target = self._next_person_here()
            if target:
                self._question(target)
                continue
            if not self._walk_toward_lead() and not self._explore():
                break
        if self.game.result is None:
            self._accuse()
        return self.game

    def _question(self, person: str) -> None:
        first = self.asked.get(person, 0) == 0
        self.asked[person] = self.asked.get(person, 0) + 1
        if first:
            before = len(self.clues)
            self.say(f"talk {person}: {QUESTION}")
            if len(self.clues) == before:        # held back: ask again, politely, showing clues
                self.asked[person] += 1
                shown = " ".join(self.clues.values())
                self.say(f"talk {person}: {POLITE} I know this much: {shown}")

    def _next_person_here(self) -> str | None:
        here = [p for p, where in self.people_at.items() if where == self.place]
        return next((p for p in here if self.asked.get(p, 0) == 0), None)

    def _walk_toward_lead(self) -> bool:
        while self.leads and self.asked.get(self.leads[0], 0):
            self.leads.popleft()
        if not self.leads:
            return False
        where = self.people_at.get(self.leads[0])
        if where is None:                         # not seen yet: explore to find them
            return self._explore()
        return self._step_toward(where)

    def _explore(self) -> bool:
        unvisited = {p for exits in self.exits.values() for p in exits} - self.visited
        if not unvisited:
            return False
        path = min((self._path(p) for p in sorted(unvisited)), key=len)
        return self._step(path)

    def _step_toward(self, place: str) -> bool:
        return self._step(self._path(place))

    def _step(self, path: list[str]) -> bool:
        if not path:
            return False
        self.say(f"go {path[0]}")
        if path[0] not in self.visited:
            self.say("look")
        return True

    def _path(self, goal: str) -> list[str]:
        prev, queue = {self.place: None}, deque([self.place])
        while queue:
            here = queue.popleft()
            if here == goal:
                path = []
                while here != self.place:
                    path.append(here)
                    here = prev[here]
                return path[::-1]
            for nxt in sorted(self.exits.get(here, ())):
                if nxt not in prev:
                    prev[nxt] = here
                    queue.append(nxt)
        return []

    # ---- deciding --------------------------------------------------------------

    def _named(self) -> dict[str, list[str]]:
        """Person -> ids of notebook clues whose text names them (first name or surname)."""
        out: dict[str, list[str]] = {}
        for person in self.people_at:
            parts = person.split()
            for clue_id, text in sorted(self.clues.items(), key=lambda kv: int(kv[0][1:])):
                if any(re.search(rf"\b{re.escape(p)}\b", text) for p in parts):
                    out.setdefault(person, []).append(clue_id)
        return out

    def _ready_to_accuse(self) -> bool:
        return any(len(ids) >= ACCUSE_AT for ids in self._named().values())

    def _accuse(self) -> None:
        named = self._named()
        if not named:
            return
        person = max(sorted(named), key=lambda p: len(named[p]))
        self.game.accusation = person
        self.say(f"accuse {person}: the clues point to them {' '.join(named[person])}")

    # ---- reading the narration ----------------------------------------------------

    def _read(self, line: str, text: str) -> None:
        m = re.search(r"^(.+?) \[\d\d:\d\d\]$", text.splitlines()[0] if text else "")
        if line == "look" and m:
            self.place = m.group(1)
            self.visited.add(self.place)
        m = re.search(r"You walk to (.+?)\. \[", text)
        if m:
            self.place = m.group(1)
        m = re.search(r"^Exits: (.+)\.$", text, re.M)
        if m and self.place:
            exits = [e.strip() for e in m.group(1).split(", ")]
            self.exits[self.place] = set(exits)
            for e in exits:
                self.exits.setdefault(e, set()).add(self.place)
        m = re.search(r"Here: (.+?)\.(?:$|\n)", text, re.M)
        if m and self.place:
            for person in m.group(1).split(", "):
                self.people_at[person] = self.place
                # a lead first heard by first name becomes this full name
                self.leads = deque(person if lead in person.split() else lead
                                   for lead in self.leads)
        for clue_id in re.findall(r"New clue in your notebook: (C\d+)", text):
            self.clues.setdefault(clue_id, "")
        for clue_text, clue_id in re.findall(r"^You notice: (.+) \[(C\d+)\]$", text, re.M):
            self.clues[clue_id] = clue_text
        if line == "notebook":
            for clue_id, clue_text in re.findall(r"^(C\d+)  (.+?)  \(", text, re.M):
                self.clues[clue_id] = clue_text
        m = re.match(r"^(.+?): \"(.*)\" \[", text, re.S)
        if m:
            reply = m.group(2)
            for name in re.findall(r"\b(?:Ask|ask) ([A-Z][a-z]+(?: [A-Z][a-z]+)?)", reply):
                person = self._person(name)
                if person and person != m.group(1) and person not in self.leads:
                    self.leads.append(person)
        m = re.search(r"^Result: (\w+)", text, re.M)
        if m:
            self.game.result = m.group(1).lower()

    def _person(self, name: str) -> str | None:
        """A full name for a first name seen in a reply; unknown names are kept as said."""
        for person in self.people_at:
            if name in person.split() or name == person:
                return person
        return name
