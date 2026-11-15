"""Plain typed commands: turn a player line into a tool call, and a tool result
into text. Used by the no-model game loop; holds no game rules."""

from __future__ import annotations

import re

HELP = ("Commands: look | go <place> | talk <person>: <what you say> | notebook | hint | "
        "accuse <person>: <motive> <clue ids, e.g. C3 C5> | help | quit")


def parse_command(line: str) -> tuple[str, dict] | None:
    """Map a typed line to (tool name, arguments), or None if it is not a command."""
    text = line.strip()
    low = text.lower()
    if low in ("look", "l", "look around"):
        return "look", {}
    if low in ("notebook", "n", "notes"):
        return "notebook", {}
    if low in ("hint", "h"):
        return "hint", {}
    m = re.match(r"go\s+(?:to\s+)?(.+)$", text, re.IGNORECASE)
    if m:
        return "go", {"place": m.group(1).strip()}
    m = re.match(r"(?:talk|speak)\s+(?:to\s+|with\s+)?([^:]+?)\s*(?::\s*(.*))?$", text,
                 re.IGNORECASE) or re.match(r"(?:ask|tell)\s+([^:]+?)\s*:\s*(.*)$", text,
                                            re.IGNORECASE)
    if m:
        return "talk", {"character": m.group(1).strip(),
                        "message": (m.group(2) or "").strip() or "Hello."}
    m = re.match(r"accuse\s+([^:]+?)\s*:\s*(.*)$", text, re.IGNORECASE)
    if m:
        rest = m.group(2)
        clue_ids = [c.upper() for c in re.findall(r"\b[cC]\d+\b", rest)]
        motive = re.sub(r"\b[cC]\d+\b", "", rest).strip(" ,;.") or "unknown"
        return "accuse", {"character": m.group(1).strip(), "motive": motive,
                          "clue_ids": clue_ids}
    return None


def render(tool: str, result: dict) -> str:
    """A plain-text view of one tool result."""
    if not result.get("ok"):
        text = f"({result.get('error', 'that did not work')})"
        if result.get("exits"):
            text += " Exits: " + ", ".join(result["exits"]) + "."
        return text
    if tool == "look":
        lines = [f"{result['place']} [{result['clock']}]", result["description"]]
        lines += [f"You notice: {c['text']} [{c['id']}]" for c in result["clues"]]
        if result["present"]:
            lines.append("Here: " + ", ".join(result["present"]) + ".")
        lines.append("Exits: " + ", ".join(result["exits"]) + ".")
        return "\n".join(lines)
    if tool == "go":
        text = f"You walk to {result['place']}. [{result['clock']}]"
        if result["present"]:
            text += " Here: " + ", ".join(result["present"]) + "."
        return text
    if tool == "notebook":
        if not result["clues"]:
            return "Your notebook is empty."
        return "\n".join(f"{c['id']}  {c['text']}  ({c['source']}, {c['place']}, {c['at']})"
                         for c in result["clues"])
    if tool == "talk":
        text = f"{result['character']}: \"{result['reply']}\" [{result['clock']}]"
        for clue_id in result["new_clues"]:
            text += f"\n(New clue in your notebook: {clue_id})"
        return text
    if tool == "hint":
        target = result["target"]
        if target is None:
            return f"Hint: you have heard what there is to hear. Time to accuse? [{result['clock']}]"
        where = f" at {target['place']}" if target.get("place") else ""
        return f"Hint: {target.get('character') or 'someone'}{where} could help. [{result['clock']}]"
    if tool == "accuse":
        lines = [f"Result: {result['result'].upper()}",
                 f"Supporting clues that count: {', '.join(result['supporting']) or 'none'}",
                 f"Missed: {', '.join(result['missed']) or 'none'}",
                 f"The culprit: {result['culprit']}", f"The motive: {result['true_motive']}",
                 "What really happened:"]
        lines += [f"  {line}" for line in result["true_timeline"]]
        return "\n".join(lines)
    return str(result)


def render_ending(end: dict) -> str:
    """The timeout ending: the true timeline, from end_turn's result."""
    lines = [f"  {line}" for line in end.get("true_timeline", ())]
    return ("What really happened:\n" + "\n".join(lines)) if lines else ""
