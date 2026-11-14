"""Plain typed commands: turn a player line into a tool call, and a tool result
into text. Used by the no-model game loop; holds no game rules."""

from __future__ import annotations

import re

HELP = "Commands: look | go <place> | notebook | help | quit"


def parse_command(line: str) -> tuple[str, dict] | None:
    """Map a typed line to (tool name, arguments), or None if it is not a command."""
    text = line.strip()
    low = text.lower()
    if low in ("look", "l", "look around"):
        return "look", {}
    if low in ("notebook", "n", "notes"):
        return "notebook", {}
    m = re.match(r"go\s+(?:to\s+)?(.+)$", text, re.IGNORECASE)
    if m:
        return "go", {"place": m.group(1).strip()}
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
    return str(result)
