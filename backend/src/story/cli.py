from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
from collections.abc import Sequence
from pathlib import Path

from src.story.runtime.contracts import PackMismatch, RuntimeGenerationUnavailable
from src.story.script_pack import PackCompileError, compile_script_pack
from src.story.script_pack.models import CompiledScriptPack, ScriptPackSourceV2
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
    init_pack = commands.add_parser("init-pack")
    init_pack.add_argument("pack_path", type=Path)
    init_pack.add_argument("--force", action="store_true")
    init_pack.add_argument("--cache-root", type=Path, default=Path("data/pack_cache"))
    return parser


async def autoplay(
    pack,
    store,
    orchestrator,
    session_id: str,
    seed: int,
    choice_strategy: str,
    max_commands: int,
    max_attempts: int = 5,
) -> SessionState:
    """Drive a Playthrough to its ending through TurnOrchestrator.

    Uses the same authoritative command flow as the HTTP ``/turns`` route:
    opening, offered choice selection, Pending Consequence recovery, and
    ending.  A failed generation leaves the committed choice pending; the
    loop resumes it with ``choice_id=None`` (same idempotency key so a
    partially committed command replays instead of appending twice).
    """
    try:
        state = store.load_session(session_id)
    except SessionNotFound:
        state = initial_session_state(pack, session_id, seed)
        store.create_session(state, pack=pack)
    if state.pack_id != pack.source.identity.id or state.pack_hash != pack.pack_hash:
        raise PackMismatch(session_id)

    commands = 0
    attempts = 0
    while True:
        state = store.load_session(session_id)
        if state.status == SessionStatus.ENDED:
            return state
        if commands >= max_commands:
            raise RuntimeError("autoplay command budget exhausted")

        if state.pending_consequence is not None:
            choice_id = None
            command_key = f"autoplay-resume-{commands}"
        elif state.pending_decision is not None:
            choice = state.pending_decision.choices[0 if choice_strategy == "first" else -1]
            choice_id = choice.id
            command_key = f"autoplay-select-{commands}"
        else:
            choice_id = None
            command_key = f"autoplay-open-{commands}"

        try:
            async for event_type, data in orchestrator.execute_turn(
                pack,
                session_id,
                state.revision,
                command_key,
                choice_id,
            ):
                if event_type == "retry_after":
                    raise RuntimeGenerationUnavailable("command already in progress")
                _print({"event": event_type, "data": data})
        except RuntimeGenerationUnavailable:
            attempts += 1
            if attempts >= max_attempts:
                raise
            await asyncio.sleep(1)
            continue

        attempts = 0
        commands += 1


async def _init_pack(
    pack: CompiledScriptPack,
    cache_root: Path,
    opening_agent,
    guard,
    force: bool = False,
) -> dict:
    """Generate the opening segment only and persist it to PackCache.

    Offline cache tooling: the cached opening passes the same deterministic
    validation and guard chain the authoritative flow uses, and may only
    seed an opening generation.  It never defines production state
    transitions — no pre-generated consequences and no implicit success
    result are ever written.
    """
    from src.story.runtime.pacing import compute_pacing_envelope
    from src.story.runtime.pack_cache import CachedOpening, PackCache
    from src.story.runtime.simulator import simulate_segment
    from src.story.runtime.validator import (
        validate_segment_draft,
        validate_segment_plan,
    )

    cache = PackCache(cache_root)
    if cache.load_opening(pack.pack_hash) is not None and not force:
        return {
            "status": "already_initialized",
            "pack_id": pack.source.identity.id,
            "pack_hash": pack.pack_hash,
        }

    state = initial_session_state(pack, "init_pack", session_seed=0)
    pacing = compute_pacing_envelope(state, pack)
    result = await opening_agent.generate(pack, state, pacing)
    plan = validate_segment_plan(pack, state, result.segment_plan, pacing)
    draft = validate_segment_draft(plan, result.segment_draft)

    guard_result = guard.check_segment(pack, state, plan, draft)
    if not guard_result.passed:
        raise RuntimeError("guard rejected opening segment")

    seg_events = simulate_segment(pack, state, plan, draft)

    cache.save_opening(
        pack.pack_hash,
        CachedOpening(
            segment_plan=plan,
            segment_draft=draft,
            seg_events=seg_events,
            pacing=pacing,
        ),
    )
    return {
        "status": "initialized",
        "pack_id": pack.source.identity.id,
        "pack_hash": pack.pack_hash,
        "opening_segment_id": plan.segment_id,
    }


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        if args.command == "validate":
            pack = compile_script_pack(args.pack_path)
            result = {
                "pack_id": pack.source.identity.id,
                "pack_hash": pack.pack_hash,
                "characters": len(pack.character_ids),
                "facts": len(pack.fact_ids),
                "goals": len(pack.goal_ids),
            }
            if isinstance(pack.source, ScriptPackSourceV2):
                result["completion_requirements"] = len(pack.completion_requirement_ids)
            else:
                endings = pack.source.endings
                result["normal_endings"] = sum(item.type != "fallback" for item in endings)
                result["fallback_endings"] = sum(item.type == "fallback" for item in endings)
            _print(result)
            return 0
        if args.command == "init-session":
            pack = compile_script_pack(args.pack_path)
            state = initial_session_state(pack, args.session_id, args.seed)
            StoryEventStore(args.database).create_session(state, pack=pack)
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
            from dotenv import load_dotenv

            from src.story.runtime.completion_judge import CompletionJudge
            from src.story.runtime.config import OpenCodeGoSettings
            from src.story.runtime.director import SdkDirector
            from src.story.runtime.guard import Guard
            from src.story.runtime.model import build_model_bundle
            from src.story.runtime.pack_cache import PackCache
            from src.story.runtime.planner import SdkPlanner
            from src.story.runtime.segment_writer import SdkSegmentWriter
            from src.story.runtime.turn_orchestrator import TurnOrchestrator
            from src.story.runtime.unified_segment import SdkUnifiedSegmentAgent

            load_dotenv()
            pack = compile_script_pack(args.pack_path)
            store = StoryEventStore(args.database)
            settings = OpenCodeGoSettings.from_env()
            bundle = build_model_bundle(settings)
            orchestrator = TurnOrchestrator(
                store,
                SdkDirector(bundle.model),
                SdkSegmentWriter(bundle.model),
                Guard(),
                CompletionJudge(),
                planner=SdkPlanner(bundle.model),
                unified_agent=SdkUnifiedSegmentAgent(bundle.model),
                pack_cache=PackCache(Path(os.getenv("GAL_PACK_CACHE_ROOT", "data/pack_cache"))),
            )
            asyncio.run(
                autoplay(
                    pack=pack,
                    store=store,
                    orchestrator=orchestrator,
                    session_id=args.session_id,
                    seed=args.seed,
                    choice_strategy=args.choice_strategy,
                    max_commands=args.max_commands,
                )
            )
            return 0
        if args.command == "init-pack":
            from dotenv import load_dotenv

            from src.story.runtime.config import OpenCodeGoSettings
            from src.story.runtime.guard import Guard
            from src.story.runtime.model import build_model_bundle
            from src.story.runtime.unified_segment import (
                OPENING_INSTRUCTIONS,
                SdkUnifiedSegmentAgent,
            )

            load_dotenv()
            pack = compile_script_pack(args.pack_path)
            settings = OpenCodeGoSettings.from_env()
            bundle = build_model_bundle(settings)
            result = asyncio.run(
                _init_pack(
                    pack=pack,
                    cache_root=args.cache_root,
                    opening_agent=SdkUnifiedSegmentAgent(
                        bundle.model, instructions=OPENING_INSTRUCTIONS
                    ),
                    guard=Guard(),
                    force=args.force,
                )
            )
            _print(result)
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
