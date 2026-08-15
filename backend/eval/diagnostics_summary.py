"""Summarize story_turn_diagnostics for eval sessions (stage timing evidence)."""

from __future__ import annotations

import argparse
import statistics
from pathlib import Path

from src.story.storage import StoryEventStore


def summarize(database: Path, sessions: list[str]) -> dict:
    store = StoryEventStore(database)
    out = {}
    for session_id in sessions:
        records = store.load_turn_diagnostics(session_id)
        stages: dict[str, list[int]] = {}
        regenerations = 0
        outcomes: dict[str, int] = {}
        density_hits = 0
        for record in records:
            outcomes[record["outcome"]] = outcomes.get(record["outcome"], 0) + 1
            regenerations += record["regenerations"]
            density_hits += len(
                [v for v in record["validator_violations"] if "density" in v]
            )
            for stage in record["stages"]:
                stages.setdefault(stage["name"], []).append(stage["duration_ms"])
        out[session_id] = {
            "commands": len(records),
            "outcomes": outcomes,
            "regenerations": regenerations,
            "density_rejections": density_hits,
            "stages_ms": {
                name: {
                    "mean": round(statistics.fmean(values)),
                    "max": max(values),
                    "attempts": len(values),
                }
                for name, values in sorted(stages.items())
            },
        }
    return out


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("database", type=Path)
    parser.add_argument("sessions", nargs="+")
    args = parser.parse_args()
    import json

    print(json.dumps(summarize(args.database, args.sessions), ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
