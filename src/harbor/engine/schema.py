"""CREATE TABLE statements for one run database.

Three groups: case data (written once by case_loader), run data (changed only
through events.record inside a tool call) and trace data (written by tracing).
"""

from __future__ import annotations

import sqlite3

CASE_TABLES = ("case_meta", "places", "exits", "characters", "clues", "knowledge",
               "pointers", "case_solution")
RUN_TABLES = ("game_state", "trust", "notebook", "conversations", "events", "runs", "interactions", "attempts")
TRACE_TABLES = ("spans", "labels")

SCHEMA = """
CREATE TABLE case_meta (
    case_id TEXT PRIMARY KEY, title TEXT NOT NULL, premise TEXT NOT NULL,
    start_place TEXT NOT NULL, start_clock TEXT NOT NULL, end_clock TEXT NOT NULL,
    nudge_order TEXT NOT NULL                       -- JSON list of clue ids
);
CREATE TABLE places (id TEXT PRIMARY KEY, name TEXT NOT NULL, description TEXT NOT NULL);
CREATE TABLE exits (from_place TEXT NOT NULL, to_place TEXT NOT NULL,
                    PRIMARY KEY (from_place, to_place));
CREATE TABLE characters (
    id TEXT PRIMARY KEY, name TEXT NOT NULL, role TEXT NOT NULL, persona TEXT NOT NULL,
    speech_style TEXT NOT NULL, start_place TEXT NOT NULL,
    schedule TEXT NOT NULL,                         -- JSON; empty until world agents
    fallback_line TEXT NOT NULL
);
CREATE TABLE clues (
    id TEXT PRIMARY KEY, text TEXT NOT NULL, kind TEXT NOT NULL, found_at_place TEXT,
    keywords TEXT NOT NULL,                         -- JSON list
    implicates TEXT NOT NULL                        -- JSON list of character ids; solver data
);
CREATE TABLE knowledge (
    character_id TEXT NOT NULL, clue_id TEXT NOT NULL,
    unlock_topics TEXT NOT NULL,                    -- JSON list; empty: any message
    min_trust INTEGER NOT NULL,
    related_clues TEXT NOT NULL,                    -- JSON list
    unlock_if_shown TEXT NOT NULL,                  -- JSON list
    PRIMARY KEY (character_id, clue_id)
);
CREATE TABLE pointers (
    character_id TEXT NOT NULL, clue_id TEXT NOT NULL, points_to_character TEXT NOT NULL,
    misleading INTEGER NOT NULL, line TEXT NOT NULL
);
CREATE TABLE case_solution (
    culprit_id TEXT NOT NULL, motive TEXT NOT NULL, method TEXT NOT NULL,
    true_timeline TEXT NOT NULL,                    -- JSON list
    required_clue_ids TEXT NOT NULL,                -- JSON list
    min_supporting INTEGER NOT NULL
);

CREATE TABLE runs (
    run_id TEXT PRIMARY KEY, seed INTEGER NOT NULL, case_id TEXT NOT NULL,
    git_commit TEXT, prompt_hash TEXT, model TEXT
);
CREATE TABLE game_state (
    run_id TEXT PRIMARY KEY,
    clock INTEGER NOT NULL,                         -- minutes since midnight
    place TEXT NOT NULL, active_character TEXT,
    turn INTEGER NOT NULL, turns_since_new_clue INTEGER NOT NULL,
    nudge_clue TEXT,
    status TEXT NOT NULL,                           -- active | ended
    result TEXT                                     -- win | partial | lose | timeout
);
CREATE TABLE trust (
    run_id TEXT NOT NULL, character_id TEXT NOT NULL, level INTEGER NOT NULL,
    PRIMARY KEY (run_id, character_id)
);
CREATE TABLE notebook (
    run_id TEXT NOT NULL, clue_id TEXT NOT NULL,
    source_character TEXT,                          -- NULL: seen on look
    place TEXT NOT NULL, minute INTEGER NOT NULL, turn INTEGER NOT NULL,
    PRIMARY KEY (run_id, clue_id)
);
CREATE TABLE conversations (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id TEXT NOT NULL, character_id TEXT NOT NULL, turn INTEGER NOT NULL,
    speaker TEXT NOT NULL,                          -- player | npc
    text TEXT NOT NULL
);
CREATE TABLE events (
    seq INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id TEXT NOT NULL, turn INTEGER NOT NULL, type TEXT NOT NULL,
    payload TEXT NOT NULL,                          -- JSON object
    span_id TEXT                                    -- the tool_call span that wrote it
);

CREATE TABLE interactions (
    run_id TEXT NOT NULL, interaction_id TEXT PRIMARY KEY, turn INTEGER NOT NULL,
    character_id TEXT NOT NULL, player_message TEXT NOT NULL,
    status TEXT NOT NULL, attempt INTEGER NOT NULL, reply TEXT, fallback INTEGER,
    outcome TEXT, new_clues TEXT
);
CREATE TABLE attempts (
    interaction_id TEXT NOT NULL, attempt INTEGER NOT NULL, status TEXT NOT NULL,
    note TEXT, staged TEXT NOT NULL, draft TEXT, verdict TEXT, verdict_reason TEXT,
    outcome TEXT, leaked TEXT, PRIMARY KEY (interaction_id, attempt)
);

CREATE TABLE spans (
    run_id TEXT NOT NULL, trace_id TEXT NOT NULL, span_id TEXT PRIMARY KEY,
    parent_span_id TEXT, kind TEXT NOT NULL, name TEXT NOT NULL,
    start_ts REAL NOT NULL, end_ts REAL NOT NULL,
    model TEXT, input_tokens INTEGER, output_tokens INTEGER,
    input TEXT, output TEXT, status TEXT NOT NULL, attrs TEXT NOT NULL
);
CREATE TABLE labels (trace_id TEXT NOT NULL, label TEXT NOT NULL, note TEXT);
"""


def connect(path: str = ":memory:") -> sqlite3.Connection:
    """Open a run database in autocommit mode with rows addressable by column name."""
    conn = sqlite3.connect(path, isolation_level=None)
    conn.row_factory = sqlite3.Row
    return conn


def create_schema(conn: sqlite3.Connection) -> None:
    conn.executescript(SCHEMA)
