"""The Claude Code plugin files (ADR 004 section 2.9), checked against the tool registry.

`claude plugin validate --strict` is run by hand (ADR 004 section 5); these tests
check what the validator cannot: that every tool the files name exists with the
right audience, that the NPC and referee agents get only their own MCP tools,
and that the drafts carry the owner's DRAFT marker.
"""

import json
import re
from pathlib import Path

import pytest

from harbor.tools import REGISTRY

ROOT = Path(__file__).resolve().parents[1]
PLUGIN, SERVER = "harbor-mystery", "harbor"
PREFIX = f"mcp__plugin_{PLUGIN}_{SERVER}__"  # documented plugin MCP tool naming
DRAFT = "<!-- DRAFT: owner to edit -->"
ADR = ROOT / "docs" / "adr" / "004-m7-plugin-and-interaction-lifecycle.md"
COMMAND = ROOT / "commands" / "harbor-mystery.md"
GAME_MASTER = ROOT / "agents" / "game-master.md"
NPC = ROOT / "agents" / "npc.md"
REFEREE = ROOT / "agents" / "referee.md"
FILES = (COMMAND, GAME_MASTER, NPC, REFEREE)

# The session tools arrive with Phase 2 (M7-T07) on another branch, so they are
# checked against the ADR 004 section 2.8 table instead of the registry.
# EMPTY THIS SET WHEN PHASE 2 IS MERGED: test_pending_tools_are_not_registered_yet
# fails as soon as the registry has any of them, to force that.
PENDING_PHASE_2 = {"new_game", "resume_game", "game_status"}


def adr_audiences():
    """Tool name -> audience, from the table in ADR 004 section 2.8."""
    text = ADR.read_text(encoding="utf-8")
    section = text.split("### 2.8")[1].split("### 2.9")[0]
    found = {}
    for line in section.splitlines():
        cells = [c.strip() for c in line.strip().strip("|").split("|")]
        if len(cells) >= 2 and cells[1] in ("gm", "npc", "referee"):
            for name in re.findall(r"`([a-z_]+)`", cells[0]):
                found[name] = cells[1]
    return found


def audience(name):
    """The audience of a tool: the registry's, or ADR 2.8's for a pending tool."""
    if name in PENDING_PHASE_2:
        return adr_audiences().get(name)
    tool = REGISTRY.get(name)
    return tool.audience if tool else None


def tools_of(aud):
    names = set(REGISTRY.names(aud))
    if aud == "gm":
        names |= PENDING_PHASE_2
    return names


ALL_TOOLS = tools_of("gm") | tools_of("npc") | tools_of("referee") | set(REGISTRY.names("engine"))


def split(path):
    """(frontmatter dict, body) for the small YAML subset these files use."""
    text = path.read_text(encoding="utf-8")
    assert text.startswith("---\n"), f"{path.name}: frontmatter must start on line 1"
    head, body = text[4:].split("\n---\n", 1)
    meta, key = {}, None
    for line in head.splitlines():
        if line.startswith("  - "):
            meta[key].append(_scalar(line[4:]))
        elif line.strip():
            key, _, value = line.partition(":")
            meta[key] = _scalar(value.strip()) if value.strip() else []
    return meta, body


def _scalar(value):
    value = value.strip()
    if value[:1] in "\"'" and value[-1:] == value[:1]:
        return value[1:-1]
    if value in ("true", "false"):
        return value == "true"
    return int(value) if value.isdigit() else value


def mcp_entries(entries):
    return [e[len(PREFIX):] for e in entries if e.startswith(PREFIX)]


# --- manifests ---------------------------------------------------------------------

def test_plugin_manifest_parses_and_points_at_the_server():
    manifest = json.loads((ROOT / ".claude-plugin" / "plugin.json").read_text(encoding="utf-8"))
    assert manifest["name"] == PLUGIN
    assert manifest["version"] and manifest["description"] and manifest["author"]["name"]
    server = manifest["mcpServers"][SERVER]
    assert server["command"] == "python3"
    assert server["args"] == ["-m", "harbor.mcp_server"]
    assert server["env"] == {"PYTHONPATH": "${CLAUDE_PLUGIN_ROOT}/src",
                             "HARBOR_HOME": "${CLAUDE_PLUGIN_DATA}"}
    # the module the server runs lives where PYTHONPATH points (it arrives in Phase 2)
    assert (ROOT / "src" / "harbor").is_dir()


def test_marketplace_manifest_parses_and_lists_the_plugin_in_place():
    market = json.loads((ROOT / ".claude-plugin" / "marketplace.json").read_text(encoding="utf-8"))
    assert market["name"] == "harbor-mystery-local"
    assert market["owner"]["name"]
    assert [(p["name"], p["source"]) for p in market["plugins"]] == [(PLUGIN, "./")]


# --- draft marker and frontmatter --------------------------------------------------

@pytest.mark.parametrize("path", FILES, ids=lambda p: p.name)
def test_every_file_is_marked_as_a_draft(path):
    # Claude Code reads frontmatter only when `---` is the file's first line (a
    # leading comment makes the validator report "No frontmatter block found"),
    # so the marker is the first line after the frontmatter.
    meta, body = split(path)
    assert meta["description"]
    assert body.splitlines()[0] == DRAFT


@pytest.mark.parametrize("path", (GAME_MASTER, NPC, REFEREE), ids=lambda p: p.name)
def test_agent_names_match_the_scoped_names_the_server_hands_out(path):
    meta, _ = split(path)
    assert meta["name"] == path.stem
    assert ":" not in meta["name"]


def test_begin_talk_names_the_plugin_agents():
    source = (ROOT / "src" / "harbor" / "tools" / "talk_tools.py").read_text(encoding="utf-8")
    for path in (NPC, REFEREE):
        assert f'"{PLUGIN}:{split(path)[0]["name"]}"' in source


# --- tool allowlists ---------------------------------------------------------------

@pytest.mark.parametrize("path, aud, turns, model", [(NPC, "npc", 10, "sonnet"),
                                                     (REFEREE, "referee", 4, "haiku")],
                         ids=["npc", "referee"])
def test_subagents_get_only_their_own_mcp_tools(path, aud, turns, model):
    meta, _ = split(path)
    tools = meta["tools"]
    assert isinstance(tools, list) and tools
    assert all(t.startswith(PREFIX) for t in tools), "no built-in tools, no Agent, no wildcards"
    assert sorted(mcp_entries(tools)) == sorted(tools_of(aud))
    assert meta["omitClaudeMd"] is True
    assert meta["maxTurns"] == turns
    assert meta["model"] == model
    assert "disallowedTools" not in meta and "mcpServers" not in meta


def test_game_master_agent_gets_the_gm_tools_and_the_two_subagents():
    meta, _ = split(GAME_MASTER)
    tools = meta["tools"]
    agent = [t for t in tools if t.startswith("Agent")]
    assert agent == ["Agent(npc, referee, harbor-mystery:npc, harbor-mystery:referee)"]
    rest = [t for t in tools if t not in agent]
    assert all(t.startswith(PREFIX) for t in rest), "no file, shell or web tools"
    assert sorted(mcp_entries(rest)) == sorted(tools_of("gm"))
    assert meta.get("model", "inherit") == "inherit"  # the session model


def test_command_preapproves_only_gm_tools():
    meta, _ = split(COMMAND)
    allowed = meta["allowed-tools"]
    assert all(t.startswith(PREFIX) for t in allowed)
    assert sorted(mcp_entries(allowed)) == sorted(tools_of("gm"))


# --- every tool named exists with the right audience -------------------------------

def named_tools(path):
    """Tool names in a file: full MCP names anywhere, bare names in backticks."""
    text = path.read_text(encoding="utf-8")
    full = set(re.findall(re.escape(PREFIX) + r"([A-Za-z0-9_]+)", text))
    bare = set(re.findall(r"`([a-z][a-z0-9_]*)`", text))
    return full, bare


# Result fields the files name (ADR 2.8 result column, and recall_knowledge's
# fields from rules.recall). Listed by hand, so a misspelt tool name cannot pass
# as a field.
RESULT_FIELDS = {"interaction_id", "attempt", "npc_agent", "referee_agent", "status", "reply",
                 "new_clues", "fallback", "previous", "run_id", "help", "summary",
                 "id", "name", "role", "persona", "speech_style", "player_message", "note",
                 "calls_left", "clues", "pointers", "line", "guarded", "trust", "shown",
                 "in_notebook", "text", "allowed_facts", "may_send_the_player_to",
                 "player_said", "facts_the_player_stated"}
# Other backticked words: the server name, the Agent tool argument,
# `/harbor-mystery:harbor-mystery` arguments (case ids, `resume`, and new_game's
# Phase 2 `case` argument), and the subagents' one-word final messages.
OTHER_WORDS = {"harbor", "subagent_type", "resume", "case", "saltmere", "gaiety", "kestrel",
               "submitted", "failed", "done"}


def non_tool_words():
    """Backticked words that are not tools: argument names and values, result fields."""
    words = RESULT_FIELDS | OTHER_WORDS
    for aud in ("gm", "npc", "referee"):
        for name in REGISTRY.names(aud):
            for key, prop in REGISTRY.get(name).input_schema["properties"].items():
                words.add(key)
                words.update(prop.get("enum", ()))
    return words - ALL_TOOLS


@pytest.mark.parametrize("path", FILES, ids=lambda p: p.name)
def test_every_tool_named_exists(path):
    full, bare = named_tools(path)
    assert full, "the file names its tools"
    for name in full:
        assert audience(name) in ("gm", "npc", "referee"), f"{name} is not a model-facing tool"
    unknown = {w for w in bare if w not in ALL_TOOLS and w not in non_tool_words()}
    assert not unknown, f"backticked names that are neither tools nor tool fields: {unknown}"
    assert not (full | bare) & set(REGISTRY.names("engine")), "engine steps are never offered"


@pytest.mark.parametrize("path, aud", [(NPC, "npc"), (REFEREE, "referee")], ids=["npc", "referee"])
def test_subagent_files_name_only_their_own_tools(path, aud):
    full, bare = named_tools(path)
    named = full | (bare & ALL_TOOLS)
    assert {audience(n) for n in named} == {aud}
    assert named == tools_of(aud)


@pytest.mark.parametrize("path", (COMMAND, GAME_MASTER), ids=lambda p: p.name)
def test_game_master_files_forbid_every_npc_and_referee_tool(path):
    text = path.read_text(encoding="utf-8")
    full, bare = named_tools(path)
    named = full | (bare & ALL_TOOLS)
    assert tools_of("gm") <= named, "the game master's own tools are all named"
    forbidden = tools_of("npc") | tools_of("referee")
    assert forbidden <= named, "every NPC and referee tool is named so it can be forbidden"
    for name in forbidden:
        for line in text.splitlines():
            if f"`{name}`" in line:
                assert "Never call" in line, f"{name} named outside a prohibition: {line}"


def test_game_master_files_give_the_subagents_only_the_ids():
    for path in (COMMAND, GAME_MASTER):
        text = path.read_text(encoding="utf-8")
        assert "interaction_id: <interaction_id>\n   attempt: <attempt>\n" in text
        for agent in ("harbor-mystery:npc", "harbor-mystery:referee"):
            assert agent in text


def test_pending_tools_match_adr_and_are_not_registered_yet():
    assert {n: adr_audiences().get(n) for n in PENDING_PHASE_2} == {n: "gm" for n in PENDING_PHASE_2}
    registered = {n for n in PENDING_PHASE_2 if REGISTRY.get(n)}
    assert not registered, f"Phase 2 is merged: remove {registered} from PENDING_PHASE_2"


def test_registry_and_adr_agree_on_every_other_tool():
    for name, aud in adr_audiences().items():
        if name not in PENDING_PHASE_2:
            assert audience(name) == aud, name


# --- case neutrality ---------------------------------------------------------------

def case_words():
    """Names that tie a text to one case: ids, whole titles, whole place names,
    the parts of character names, and roles. Titles and place names are matched
    as whole phrases, so ordinary words inside them ("The Water That Went Uphill",
    "Supply Room") do not count on their own."""
    words = {"villager", "village", "fishing", "fisherman", "harbor", "harbour", "quay",
             "theatre", "theater", "stage", "observatory", "mountain", "telescope"}
    for path in (ROOT / "cases").glob("*.json"):
        case = json.loads(path.read_text(encoding="utf-8"))
        words.add(case["case_id"])
        words.add(case["title"])
        words.update(place["name"] for place in case["places"])
        for character in case["characters"]:
            words.update(part for part in character["name"].split() if len(part) > 3)
            words.add(character["role"])
    return {w.lower().strip(".,'") for w in words if len(w) > 3}


@pytest.mark.parametrize("path", (NPC, REFEREE), ids=lambda p: p.name)
def test_subagent_prompts_assume_no_setting(path):
    _, body = split(path)
    found = {w for w in case_words() if re.search(rf"\b{re.escape(w)}\b", body, re.I)}
    assert not found, f"{path.name} names a setting: {found}"


@pytest.mark.parametrize("path", (COMMAND, GAME_MASTER), ids=lambda p: p.name)
def test_tools_called_free_are_exempt_from_the_action_cap(path):
    source = (ROOT / "src" / "harbor" / "tools" / "registry.py").read_text(encoding="utf-8")
    exempt = re.search(r'ctx\.turn_open and name not in \(([^)]*)\)', source).group(1)
    lines = [l for l in path.read_text(encoding="utf-8").splitlines() if " free" in l]
    assert lines
    for line in lines:
        claim = line.split(" free")[0]
        free = set(re.findall(r"`([a-z_]+)`", claim.rsplit(". ", 1)[-1])) & ALL_TOOLS  # the sentence saying "free"
        assert free and all(f'"{name}"' in exempt for name in free), (free, exempt)
