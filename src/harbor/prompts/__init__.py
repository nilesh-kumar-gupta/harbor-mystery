"""The prompt files and their hash (stored on each run record)."""

from __future__ import annotations

import hashlib
from pathlib import Path

PROMPT_DIR = Path(__file__).resolve().parent
NAMES = ("game_master", "npc", "referee")


def load(name: str) -> str:
    return (PROMPT_DIR / f"{name}.md").read_text(encoding="utf-8")


def prompt_hash() -> str:
    digest = hashlib.sha256()
    for name in NAMES:
        digest.update(name.encode() + b"\0" + load(name).encode("utf-8") + b"\0")
    return digest.hexdigest()[:16]
