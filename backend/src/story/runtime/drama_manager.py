"""Deterministic Beat Map navigation (the DramaManager).

Packs with a ``structure`` navigate by author intent: the DramaManager picks
the next eligible beats, packs consecutive scene beats plus a terminating
decision/ending beat into one :class:`SegmentPlan`, and hands the plan to the
Segment Writer to perform.  What happens next is an authoring decision, not a
model decision — the LLM only performs the beat.

Navigation is pure: same pack + same state + same pacing ⇒ same plan.
"""

from __future__ import annotations

from src.story.conditions import ConditionEvaluationError
from src.story.runtime.context import build_condition_context
from src.story.runtime.contracts import (
    ActionResolution,
    ChoicePlan,
    RelationshipDelta,
    ScenePlan,
)
from src.story.runtime.segment_contracts import (
    EndingProposal,
    PacingEnvelope,
    SegmentPlan,
)
from src.story.script_pack.models import (
    BeatSource,
    CompiledScriptPack,
    EndingSeedSource,
    ScriptPackSourceV2,
)
from src.story.state import SessionState

# Scenes per beat-driven segment when the act gives no explicit budget:
# the terminating beat plus one scene beat.
_DEFAULT_SEGMENT_SCENES = 2

# Hard ceiling regardless of act budgets: long segments drift, beats repeat.
_MAX_SEGMENT_SCENES = 4

#: Deterministic ids: the scene index this segment will occupy keeps ids
#: unique across retries and replays without a random source.
def _scene_index(state: SessionState) -> int:
    return state.world.scene_count + 1


def beat_structure(pack: CompiledScriptPack) -> ScriptPackSourceV2 | None:
    """Return the v2 source when the pack carries a Beat Map structure."""
    source = pack.source
    if isinstance(source, ScriptPackSourceV2) and source.structure is not None:
        return source
    return None


def _ordered_beats(source: ScriptPackSourceV2) -> list[tuple[int, int, BeatSource]]:
    """All beats in (act, declaration) order — the author's spine."""
    return [
        (act_index, beat_index, beat)
        for act_index, act in enumerate(source.structure.acts)
        for beat_index, beat in enumerate(act.beats)
    ]


def _requires_met(pack: CompiledScriptPack, state: SessionState, key: str) -> bool:
    program = pack.conditions.get(key)
    if program is None:
        return True
    try:
        return bool(program.evaluate(build_condition_context(state)))
    except ConditionEvaluationError:
        # An unevaluable condition is a failed condition: the beat/seed is
        # not eligible this turn.
        return False


def _beat_eligible(
    pack: CompiledScriptPack,
    state: SessionState,
    beat: BeatSource,
    *,
    scene_index: int,
    completed: frozenset[str],
) -> bool:
    if beat.id in completed:
        return False
    if not all(target in completed for target in beat.responds_to):
        return False
    if beat.position is not None and not (
        beat.position.min_scene <= scene_index <= beat.position.max_scene
    ):
        return False
    return not (
        beat.requires and not _requires_met(pack, state, f"beat.{beat.id}.requires")
    )


def _select_seed(
    pack: CompiledScriptPack,
    state: SessionState,
) -> EndingSeedSource | None:
    """Highest-priority eligible ending seed; fallbacks are the last resort."""
    source = pack.source
    assert isinstance(source, ScriptPackSourceV2)
    eligible = [
        seed
        for seed in source.ending_seeds
        if seed.fallback or _requires_met(pack, state, f"seed.{seed.id}.requires")
    ]
    if not eligible:
        return None
    eligible.sort(key=lambda seed: (seed.fallback, -seed.priority, seed.id))
    return eligible[0]


def _ending_proposal(
    pack: CompiledScriptPack,
    state: SessionState,
    beat: BeatSource,
) -> EndingProposal:
    seed = _select_seed(pack, state)
    if seed is not None:
        return EndingProposal(
            title=seed.title[:120],
            tone=seed.tone[:80],
            terminal_state_summary=seed.frame[:600],
        )
    return EndingProposal(
        title=beat.purpose[:120],
        tone="cathartic",
        terminal_state_summary=beat.purpose[:600],
    )


def _choice_plan(beat: BeatSource, choice) -> ChoicePlan:
    return ChoicePlan(
        option_id=choice.option_id,
        action_id=choice.action_id,
        intent=choice.intent,
        target_character_id=choice.target_character_id,
        stance_axis=choice.stance_axis,
        stance_value=choice.stance_value,
        accepted_risk=choice.accepted_risk,
        potential_obligation_kind=choice.potential_obligation_kind,
        conflict_axis_id=choice.conflict_axis_id,
    )


def _scene_for_beat(
    state: SessionState,
    beat: BeatSource,
    *,
    scene_index: int,
    terminal: str,
) -> ScenePlan:
    sketch = beat.sketch
    scene_id = f"scene_s{scene_index}_{beat.id}"
    summary = beat.purpose[:200]
    return ScenePlan(
        scene_id=scene_id,
        summary=summary,
        location_id=sketch.location_id if sketch is not None else state.world.location_id,
        present_character_ids=(
            sketch.present_character_ids
            if sketch is not None
            else state.world.present_character_ids
        ),
        terminal=terminal,
        decision_id=f"dec_{scene_id}" if terminal == "decision" else None,
        choices=(
            tuple(_choice_plan(beat, choice) for choice in beat.choices)
            if terminal == "decision"
            else ()
        ),
    )


def plan_next_segment(
    pack: CompiledScriptPack,
    state: SessionState,
    pacing: PacingEnvelope,
) -> SegmentPlan | None:
    """Deterministically plan the next beat-driven segment.

    Navigation is act-contained: the current act is the first act with
    incomplete beats, and only its beats are considered — later acts never
    jump the queue.  Within the act, scene beats accumulate as ``continue``
    scenes (a decision beat may respond to a scene beat performed earlier in
    the same segment) and the first eligible decision/ending beat terminates
    the segment.  Returns ``None`` when the map offers no eligible step —
    the caller falls back to the improvisation path (the safety net, not
    the mainline).
    """
    source = beat_structure(pack)
    if source is None:
        return None

    acts = source.structure.acts
    # Optional beats never gate act progression: an act is exhausted once its
    # mandatory beats are done, so choice-dependent conditional scenes cannot
    # deadlock later acts.
    current_act = next(
        (
            act
            for act in acts
            if any(
                b.id not in state.drama.completed_beat_ids and not b.optional
                for b in act.beats
            )
        ),
        None,
    )
    if current_act is None:
        return None

    scene_index = _scene_index(state)
    scene_cap = (
        max(current_act.scene_budget)
        if current_act.scene_budget
        else _DEFAULT_SEGMENT_SCENES
    )
    scene_cap = max(1, min(scene_cap, pacing.remaining_budget, _MAX_SEGMENT_SCENES))
    ending_allowed = pacing.must_end or pacing.can_end

    ordered = sorted(
        enumerate(current_act.beats), key=lambda item: (-item[1].priority, item[0])
    )
    scene_beats: list[BeatSource] = []
    terminator: BeatSource | None = None
    for _beat_index, beat in ordered:
        if beat.id in state.drama.completed_beat_ids:
            continue
        if beat.kind == "ending" and not ending_allowed:
            continue
        # Beats performed earlier in this segment count as responded-to.
        completed = state.drama.completed_beat_ids | {b.id for b in scene_beats}
        if not _beat_eligible(
            pack, state, beat, scene_index=scene_index, completed=completed
        ):
            continue
        if beat.kind in ("decision", "ending"):
            terminator = beat
            break
        if len(scene_beats) >= scene_cap - 1:
            break
        scene_beats.append(beat)

    if terminator is None:
        # Scene beats with no eligible terminating decision/ending beat: let
        # the improvisation path drive this turn (a decision scene requires
        # authored choices we refuse to synthesize).
        return None

    scenes: list[ScenePlan] = []
    beats_performed: list[str] = []
    for offset, beat in enumerate(scene_beats):
        scenes.append(
            _scene_for_beat(
                state, beat, scene_index=scene_index + offset, terminal="continue"
            )
        )
        beats_performed.append(beat.id)

    terminal_kind: str
    if terminator.kind == "ending":
        terminal_kind = "ending"
        scenes.append(
            _scene_for_beat(
                state,
                terminator,
                scene_index=scene_index + len(scenes),
                terminal="ending",
            )
        )
        beats_performed.append(terminator.id)
        proposal = _ending_proposal(pack, state, terminator)
    else:
        terminal_kind = "decision"
        scenes.append(
            _scene_for_beat(
                state,
                terminator,
                scene_index=scene_index + len(scenes),
                terminal="decision",
            )
        )
        beats_performed.append(terminator.id)
        proposal = None

    if not scenes:
        return None

    segment_id = f"seg_s{scene_index}_{beats_performed[0] if beats_performed else 'improv'}"
    return SegmentPlan(
        segment_id=segment_id,
        scenes=tuple(scenes),
        terminal=terminal_kind,  # type: ignore[arg-type]
        ending_proposal=proposal,
        beat_ids=tuple(beats_performed),
    )


def authored_choice_resolution(pack, pending) -> ActionResolution | None:
    """Deterministic ActionResolution for an authored beat choice.

    The author already wrote the choice's outcome and relationship deltas;
    resolving it needs no model call (the planner layer is removed for
    beat-driven packs).  Returns ``None`` when the pending option is not an
    authored beat choice — the improvisation path resolves those.
    """
    source = beat_structure(pack)
    if source is None:
        return None
    for act in source.structure.acts:
        for beat in act.beats:
            for choice in beat.choices:
                if (
                    choice.option_id == pending.option_id
                    and choice.action_id == pending.action_id
                ):
                    return ActionResolution(
                        action_id=choice.action_id,
                        outcome=choice.outcome,
                        relationship_deltas=tuple(
                            RelationshipDelta(
                                character_id=delta.character_id,
                                axis=delta.axis,
                                delta=delta.delta,
                            )
                            for delta in choice.relationship_deltas
                        ),
                    )
    return None


def beat_briefs(
    pack: CompiledScriptPack,
    plan: SegmentPlan,
) -> list[dict[str, object]]:
    """Writer-facing brief per performed beat (order-aligned with scenes).

    ``must_include`` lines are prose directives — the performer must land
    them visibly in the scene, in natural language, never as ids.
    """
    source = beat_structure(pack)
    if source is None or not plan.beat_ids:
        return []
    beats = {beat.id: beat for _act_index, _beat_index, beat in _ordered_beats(source)}
    briefs: list[dict[str, object]] = []
    for scene, beat_id in zip(plan.scenes, plan.beat_ids):
        beat = beats.get(beat_id)
        if beat is None:
            continue
        briefs.append(
            {
                "scene_id": scene.scene_id,
                "beat_id": beat.id,
                "purpose": beat.purpose,
                "must_include": list(beat.must_include),
                "choices_authored": [choice.label for choice in beat.choices],
            }
        )
    return briefs


def seed_must_address(pack: CompiledScriptPack, plan: SegmentPlan) -> list[str]:
    """The selected ending seed's must-address list for an ending segment."""
    source = beat_structure(pack)
    if source is None or plan.terminal != "ending":
        return []
    for seed in source.ending_seeds:
        if seed.title[:120] == (plan.ending_proposal.title if plan.ending_proposal else ""):
            return list(seed.must_address)
    return []
