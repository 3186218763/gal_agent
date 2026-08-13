from __future__ import annotations

import argparse
import asyncio
import json
import sys
from collections.abc import Sequence
from pathlib import Path

from openai import OpenAIError

from src.story.runtime.contracts import ActionResolution, ModelContractError, PackMismatch
from src.story.runtime.validator import ProposalRejected
from src.story.script_pack import PackCompileError, compile_script_pack
from src.story.script_pack.models import CompiledScriptPack, ScriptPackSourceV2
from src.story.state import (
    EventEnvelope,
    SessionState,
    SessionStatus,
    apply_events,
    initial_session_state,
)
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
            scene = await runtime.advance(
                pack,
                session_id,
                expected_revision=state.revision,
                idempotency_key=f"autoplay-advance-{commands}",
            )
            _print(scene.model_dump(mode="json"))
        state = store.load_session(session_id)
        commands += 1
    return state


async def _init_pack(
    pack: CompiledScriptPack,
    cache_root: Path,
    opening_agent,
    unified_agent,
    planner,
    guard,
    force: bool = False,
) -> dict:
    """Generate opening + pregen, persist to PackCache.

    Returns summary dict for CLI output.
    """
    from src.story.runtime.pacing import compute_pacing_envelope
    from src.story.runtime.pack_cache import CachedOpening, CachedPregen, PackCache
    from src.story.runtime.simulator import simulate_resolution, simulate_segment
    from src.story.runtime.validator import (
        validate_action_resolution,
        validate_segment_draft,
        validate_segment_plan,
    )

    cache = PackCache(cache_root)

    # Build initial state.
    state = initial_session_state(pack, "init_pack", session_seed=0)

    # Skip opening generation if already cached (resume mode).
    cached_opening = cache.load_opening(pack.pack_hash)
    if cached_opening is not None and not force:
        plan = cached_opening.segment_plan
        draft = cached_opening.segment_draft
        seg_events = cached_opening.seg_events
        pacing = cached_opening.pacing
    else:
        # Generate opening segment.
        pacing = compute_pacing_envelope(state, pack)
        result = await opening_agent.generate(pack, state, pacing)
        plan = validate_segment_plan(pack, state, result.segment_plan, pacing)
        draft = validate_segment_draft(plan, result.segment_draft)

        guard_result = guard.check_segment(pack, state, plan, draft)
        if not guard_result.passed:
            raise RuntimeError("guard rejected opening segment")

        seg_events = simulate_segment(pack, state, plan, draft)

        # Save opening.
        cache.save_opening(
            pack.pack_hash,
            CachedOpening(
                segment_plan=plan,
                segment_draft=draft,
                seg_events=seg_events,
                pacing=pacing,
            ),
        )

    # Build post-opening state for pre-generation.
    envelopes = tuple(
        EventEnvelope(
            session_id="init_pack",
            sequence=state.revision + i,
            event=e,
        )
        for i, e in enumerate(seg_events, start=1)
    )
    post_state = apply_events(state, envelopes)

    choice_ids: list[str] = []
    if post_state.pending_decision is not None:
        for choice in post_state.pending_decision.choices:
            choice_ids.append(choice.id)

    # Pre-generate each choice (skip already-cached ones).
    pregen_count = 0
    for choice in post_state.pending_decision.choices if post_state.pending_decision else []:
        if cache.load_pregen(pack.pack_hash, choice.id) is not None:
            pregen_count += 1
            continue
        try:
            try:
                resolution = await planner.resolve_action(pack, post_state, choice)
                resolution = validate_action_resolution(
                    pack,
                    post_state,
                    resolution,
                    expected_action_id=choice.action_id,
                )
            except (ModelContractError, OpenAIError, ProposalRejected):
                # Planner may return inconsistent action_ids on flash models.
                # Fall back to a default resolution so pre-gen can proceed.
                resolution = ActionResolution(action_id=choice.action_id, outcome="success")
            pre_events = simulate_resolution(
                post_state,
                choice,
                resolution,
                idempotency_key=f"initpack-{choice.id}",
            )
            pre_envelopes = tuple(
                EventEnvelope(
                    session_id="init_pack",
                    sequence=post_state.revision + i,
                    event=e,
                )
                for i, e in enumerate(pre_events, start=1)
            )
            hypo_state = apply_events(post_state, pre_envelopes)
            hypo_pacing = compute_pacing_envelope(hypo_state, pack)

            seg_result = await unified_agent.generate(pack, hypo_state, hypo_pacing)
            seg_plan = validate_segment_plan(pack, hypo_state, seg_result.segment_plan, hypo_pacing)
            seg_draft = validate_segment_draft(seg_plan, seg_result.segment_draft)

            seg_guard = guard.check_segment(pack, hypo_state, seg_plan, seg_draft)
            if not seg_guard.passed:
                continue

            pre_seg_events = simulate_segment(pack, hypo_state, seg_plan, seg_draft)

            cache.save_pregen(
                pack.pack_hash,
                choice.id,
                CachedPregen(
                    choice_id=choice.id,
                    pre_events=pre_events,
                    seg_events=pre_seg_events,
                    segment_plan=seg_plan,
                    segment_draft=seg_draft,
                    pacing=hypo_pacing,
                ),
            )
            pregen_count += 1
        except Exception:
            # Individual pre-gen failure is non-fatal.
            import logging

            logging.getLogger(__name__).debug(
                "init-pack pre-gen failed for a choice", exc_info=True
            )

    return {
        "status": "initialized",
        "pack_id": pack.source.identity.id,
        "pack_hash": pack.pack_hash,
        "opening_segment_id": plan.segment_id,
        "choice_ids": choice_ids,
        "pregen_count": pregen_count,
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
            from dotenv import load_dotenv

            from src.story.runtime.config import OpenCodeGoSettings
            from src.story.runtime.model import build_model_bundle
            from src.story.runtime.planner import SdkPlanner
            from src.story.runtime.service import RuntimeService
            from src.story.runtime.writer import SdkWriter

            load_dotenv()
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
        if args.command == "init-pack":
            from dotenv import load_dotenv

            from src.story.runtime.config import OpenCodeGoSettings
            from src.story.runtime.guard import Guard
            from src.story.runtime.model import build_model_bundle
            from src.story.runtime.pack_cache import PackCache
            from src.story.runtime.planner import SdkPlanner
            from src.story.runtime.unified_segment import (
                OPENING_INSTRUCTIONS,
                SdkUnifiedSegmentAgent,
            )

            load_dotenv()
            pack = compile_script_pack(args.pack_path)
            cache = PackCache(args.cache_root)

            # If opening exists and --force not set, check if pregen is complete.
            # _init_pack will resume and only generate missing pregen files.
            cached_opening = cache.load_opening(pack.pack_hash)
            if cached_opening is not None and not args.force:
                # Check completeness — count expected choices from the opening.
                from src.story.state import EventEnvelope, apply_events

                state = initial_session_state(pack, "check", session_seed=0)
                envelopes = tuple(
                    EventEnvelope(
                        session_id="check",
                        sequence=state.revision + i,
                        event=e,
                    )
                    for i, e in enumerate(cached_opening.seg_events, start=1)
                )
                post_state = apply_events(state, envelopes)
                choice_ids = (
                    [c.id for c in post_state.pending_decision.choices]
                    if post_state.pending_decision
                    else []
                )
                if cache.is_complete(pack.pack_hash, choice_ids):
                    _print(
                        {
                            "status": "already_initialized",
                            "pack_id": pack.source.identity.id,
                            "pack_hash": pack.pack_hash,
                        }
                    )
                    return 0

            settings = OpenCodeGoSettings.from_env()
            bundle = build_model_bundle(settings)
            opening_agent = SdkUnifiedSegmentAgent(bundle.model, instructions=OPENING_INSTRUCTIONS)
            unified_agent = SdkUnifiedSegmentAgent(bundle.model)
            planner = SdkPlanner(bundle.model)
            guard = Guard()

            result = asyncio.run(
                _init_pack(
                    pack=pack,
                    cache_root=args.cache_root,
                    opening_agent=opening_agent,
                    unified_agent=unified_agent,
                    planner=planner,
                    guard=guard,
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
