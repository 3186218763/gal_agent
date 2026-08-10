from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Sequence
from pathlib import Path

from src.story.script_pack import PackCompileError, compile_script_pack
from src.story.state import initial_session_state
from src.story.storage import SessionAlreadyExists, SessionNotFound, StoryEventStore


def _print(payload: dict) -> None:
    print(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True))


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="python -m src.story.cli")
    commands = parser.add_subparsers(dest="command", required=True)
    validate = commands.add_parser("validate")
    validate.add_argument("pack_path", type=Path)
    initialize = commands.add_parser("init-session")
    initialize.add_argument("pack_path", type=Path)
    initialize.add_argument("--database", type=Path, required=True)
    initialize.add_argument("--session-id", required=True)
    initialize.add_argument("--seed", type=int, required=True)
    inspect = commands.add_parser("inspect-session")
    inspect.add_argument("session_id")
    inspect.add_argument("--database", type=Path, required=True)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        if args.command == "validate":
            pack = compile_script_pack(args.pack_path)
            endings = pack.source.endings
            _print({"pack_id": pack.source.identity.id, "pack_hash": pack.pack_hash, "characters": len(pack.character_ids), "facts": len(pack.fact_ids), "goals": len(pack.goal_ids), "normal_endings": sum(item.type != "fallback" for item in endings), "fallback_endings": sum(item.type == "fallback" for item in endings)})
            return 0
        if args.command == "init-session":
            pack = compile_script_pack(args.pack_path)
            state = initial_session_state(pack, args.session_id, args.seed)
            StoryEventStore(args.database).create_session(state)
            _print({"session_id": state.session_id, "pack_id": state.pack_id, "pack_hash": state.pack_hash, "revision": state.revision})
            return 0
        state = StoryEventStore(args.database).load_session(args.session_id)
        _print({"session_id": state.session_id, "pack_id": state.pack_id, "pack_hash": state.pack_hash, "revision": state.revision, "phase": state.world.phase.value, "scene_count": state.world.scene_count, "status": state.status.value})
        return 0
    except (PackCompileError, SessionAlreadyExists, SessionNotFound) as exc:
        print(str(exc), file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
