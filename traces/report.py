"""Report durable per-run metrics: python traces/report.py RUN.sqlite [--json]."""
import argparse
from collections import Counter
import json
from pathlib import Path
import sqlite3


def report(path):
    conn = sqlite3.connect(Path(path).resolve().as_uri() + "?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    try:
        run = conn.execute("SELECT run_id FROM runs").fetchone()
        if run is None:
            raise ValueError("database contains no run")
        run_id = run["run_id"]
        interactions = conn.execute("SELECT * FROM interactions WHERE run_id=?", (run_id,)).fetchall()
        attempts = conn.execute("SELECT a.* FROM attempts a JOIN interactions i USING(interaction_id) WHERE i.run_id=?", (run_id,)).fetchall()
        spans = conn.execute("SELECT * FROM spans WHERE run_id=?", (run_id,)).fetchall()
        st = conn.execute("SELECT status, result FROM game_state WHERE run_id=?", (run_id,)).fetchone()
        completed = sum(r["status"] == "committed" for r in interactions)
        fallback = sum(bool(r["fallback"]) for r in interactions)
        counts = Counter(a["interaction_id"] for a in attempts)
        outcomes = Counter(a["outcome"] for a in attempts if a["outcome"])
        npc = Counter(s["name"] for s in spans if s["kind"] == "tool_call" and
                      json.loads(s["attrs"]).get("game.audience") == "npc")
        metadata = [json.loads(s["attrs"])["mcp.meta"] for s in spans if "mcp.meta" in json.loads(s["attrs"])]
        return {"run_id": run_id, "interactions": len(interactions), "completed_interactions": completed,
                "completion_rate": completed / len(interactions) if interactions else 0,
                "game_status": st["status"] if st else "not_started", "game_result": st["result"] if st else None,
                "attempts": len(attempts), "attempts_per_interaction": dict(counts),
                "mean_attempts_per_interaction": len(attempts) / len(interactions) if interactions else 0,
                "outcomes": dict(outcomes), "fallbacks": fallback,
                "fallback_rate": fallback / completed if completed else 0,
                "invalid_verdicts": sum(a["verdict"] == "INVALID" for a in attempts),
                "missing_verdicts": outcomes.get("no_verdict", 0),
                "npc_tool_calls": sum(npc.values()), "npc_tool_calls_by_name": dict(npc),
                "mcp_meta_seen": list({json.dumps(m, sort_keys=True): m for m in metadata}.values()),
                "mcp_meta_count": len(metadata),
                "first_word_latency": "unavailable"}
    finally:
        conn.close()


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("database", type=Path)
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)
    try:
        metrics = report(args.database)
    except (OSError, ValueError, sqlite3.Error) as exc:
        parser.exit(1, f"report: {exc}\n")
    if args.json:
        print(json.dumps(metrics, sort_keys=True))
    else:
        for name, value in metrics.items():
            print(f"{name}: {json.dumps(value) if isinstance(value, (dict, list)) else value}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
