"""The architecture rules from CLAUDE.md, checked on the source itself."""

import ast
from pathlib import Path

from harbor.tools import REGISTRY

SRC = Path(__file__).parent.parent / "src" / "harbor"
# layer -> harbor subpackages or modules it must not import
FORBIDDEN = {
    "engine": {"agents", "tools", "llm", "cli", "referee", "bot", "session", "offline",
               "commands"},
    "tools": {"agents", "cli", "bot", "session", "offline"},
    "referee": {"agents", "cli", "bot", "session", "offline"},
    "llm": {"agents", "tools", "engine", "cli", "referee", "bot", "session", "offline",
            "commands", "tracing"},
    "agents": {"cli", "bot", "session", "engine"},
    "tracing": {"agents", "tools", "engine", "cli", "llm", "referee"},
}


def imports(path):
    """Every module name imported anywhere in a file."""
    found = set()
    for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
        if isinstance(node, ast.Import):
            found.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            found.add(node.module)
            found.update(f"{node.module}.{alias.name}" for alias in node.names)
    return found


def layer_of(path):
    return path.relative_to(SRC).parts[0].removesuffix(".py")


def test_imports_run_one_way():
    problems = []
    for path in SRC.rglob("*.py"):
        banned = FORBIDDEN.get(layer_of(path), set())
        for name in imports(path):
            parts = name.split(".")
            if parts[0] == "harbor" and len(parts) > 1 and parts[1] in banned:
                problems.append(f"{path.relative_to(SRC)} imports {name}")
    assert problems == []


def test_only_the_hosted_client_imports_anthropic():
    users = [str(p.relative_to(SRC)) for p in SRC.rglob("*.py")
             if any(n.split(".")[0] == "anthropic" for n in imports(p))]
    assert set(users) <= {"llm/hosted.py"}


def test_only_standard_library_and_harbor_are_imported():
    import sys
    allowed = set(sys.stdlib_module_names) | {"harbor", "anthropic"}
    for path in SRC.rglob("*.py"):
        assert {n.split(".")[0] for n in imports(path)} <= allowed, path


def test_case_solution_is_read_only_by_scoring():
    readers = [str(p.relative_to(SRC)) for p in SRC.rglob("*.py")
               if "case_solution" in p.read_text(encoding="utf-8")]
    # schema creates the table, case_loader fills it once, scoring reads it
    assert set(readers) <= {"engine/schema.py", "engine/case_loader.py", "engine/scoring.py"}


def test_the_solution_object_is_read_only_by_the_loader_and_solver():
    import re
    readers = {str(p.relative_to(SRC)) for p in SRC.rglob("*.py")
               if re.search(r"\.solution\b", p.read_text(encoding="utf-8"))}
    assert readers <= {"engine/case_loader.py", "solver.py"}


def test_npc_tools_cannot_name_a_character():
    for schema in REGISTRY.schemas("npc"):
        for prop in schema["input_schema"]["properties"]:
            assert "character" not in prop and prop not in ("who", "npc", "name"), schema["name"]


def test_engine_steps_are_never_offered_to_a_model():
    offered = {s["name"] for a in ("gm", "npc") for s in REGISTRY.schemas(a)}
    assert not offered & set(REGISTRY.names("engine"))


def test_only_the_reducer_writes_run_tables():
    """SQL writes live in four places: the event reducer (run state), the case loader
    (case tables, once), new_game (the runs row, once) and tracing (spans)."""
    import re
    allowed = {"engine/events.py", "engine/case_loader.py", "engine/state.py", "tracing.py"}
    writers = {str(p.relative_to(SRC)) for p in SRC.rglob("*.py")
               if re.search(r"\b(INSERT|UPDATE|DELETE|REPLACE)\b", p.read_text(encoding="utf-8"))}
    assert writers <= allowed
    state_src = (SRC / "engine" / "state.py").read_text(encoding="utf-8")
    write = r"\b(?:INSERT INTO|UPDATE|DELETE FROM)\s+(\w+)"
    assert re.findall(write, state_src) == ["runs"]
    assert set(re.findall(write, (SRC / "tracing.py").read_text(encoding="utf-8"))) == {"spans"}
