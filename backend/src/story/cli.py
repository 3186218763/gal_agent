from __future__ import annotations

import argparse
import asyncio
import json
import sys
from collections.abc import Sequence
from pathlib import Path

from src.story.runtime.contracts import PackMismatch
from src.story.script_pack import PackCompileError, compile_script_pack
from src.story.state import SessionState, SessionStatus, initial_session_state
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
    play = commands.add_parser("play-live")
    play.add_argument("pack_path", type=Path)
    play.add_argument("--database", type=Path, required=True)
    play.add_argument("--session-id", required=True)
    play.add_argument("--seed", type=int, required=True)
    play.add_argument("--choice-strategy", choices=("first", "last"), default="first")
    play.add_argument("--max-commands", type=int, default=200)
    return parser


async def autoplay(
    pack,
    store,
    runtime,
    session_id: str,
    seed: int,
    choice_strategy: str,
    max_commands: int,
) -> SessionState:
    try:
        state = store.load_session(session_id)
    except SessionNotFound:
        state = initial_session_state(pack, session_id, seed)
        store.create_session(state)
    if state.pack_id != pack.source.identity.id or state.pack_hash != pack.pack_hash:
        raise PackMismatch(session_id)
    commands = 0
    while state.status != SessionStatus.ENDED:
        if commands >= max_commands:
            raise RuntimeError("autoplay command budget exhausted")
        if state.pending_decision:
            choice = state.pending_decision.choices[0 if choice_strategy == "first" else -1]
            result = await runtime.select_choice(
                pack,
                session_id,
                choice.id,
                expected_revision=state.revision,
                idempotency_key=f"autoplay-{commands}",
            )
            _print(result.model_dump(mode="json"))
        else:
            scene = await runtime.advance(pack, session_id, expected_revision=state.revision)
            _print(scene.model_dump(mode="json"))
        state = store.load_session(session_id)
        commands += 1
    return state


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        if args.command == "validate":
            pack = compile_script_pack(args.pack_path)
            endings = pack.source.endings
            _print(
                {
                    "pack_id": pack.source.identity.id,
                    "pack_hash": pack.pack_hash,
                    "characters": len(pack.character_ids),
                    "facts": len(pack.fact_ids),
                    "goals": len(pack.goal_ids),
                    "normal_endings": sum(item.type != "fallback" for item in endings),
                    "fallback_endings": sum(item.type == "fallback" for item in endings),
                }
            )
            return 0
        if args.command == "init-session":
            pack = compile_script_pack(args.pack_path)
            state = initial_session_state(pack, args.session_id, args.seed)
            StoryEventStore(args.database).create_session(state)
            _print(
                {
                    "session_id": state.session_id,
                    "pack_id": state.pack_id,
                    "pack_hash": state.pack_hash,
                    "revision": state.revision,
                }
            )
            return 0
        if args.command == "inspect-session":
            state = StoryEventStore(args.database).load_session(args.session_id)
            _print(
                {
                    "session_id": state.session_id,
                    "pack_id": state.pack_id,
                    "pack_hash": state.pack_hash,
                    "revision": state.revision,
                    "phase": state.world.phase.value,
                    "scene_count": state.world.scene_count,
                    "status": state.status.value,
                }
            )
            return 0
        if args.command == "play-live":
            from src.story.runtime.config import OpenCodeGoSettings
            from src.story.runtime.model import build_model_bundle
            from src.story.runtime.planner import SdkPlanner
            from src.story.runtime.service import RuntimeService
            from src.story.runtime.writer import SdkWriter

            pack = compile_script_pack(args.pack_path)
            store = StoryEventStore(args.database)
            settings = OpenCodeGoSettings.from_env()
            bundle = build_model_bundle(settings)
            runtime = RuntimeService(
                store,
                SdkPlanner(bundle.model),
                SdkWriter(bundle.model),
            )
            asyncio.run(
                autoplay(
                    pack=pack,
                    store=store,
                    runtime=runtime,
                    session_id=args.session_id,
                    seed=args.seed,
                    choice_strategy=args.choice_strategy,
                    max_commands=args.max_commands,
                )
            )
            return 0
        raise AssertionError(f"unhandled command: {args.command}")
    except (
        PackCompileError,
        SessionAlreadyExists,
        SessionNotFound,
        PackMismatch,
        RuntimeError,
    ) as exc:
        print(str(exc), file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
