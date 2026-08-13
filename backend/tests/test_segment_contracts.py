from __future__ import annotations

import pytest
from agents.agent_output import AgentOutputSchema
from pydantic import ValidationError

from src.story.runtime.contracts import (
    ChoicePlan,
    DirectorOutput,
    EndingDraft,
    NarrativeBlock,
    SceneDraft,
    SegmentWriterOutput,
)
from src.story.runtime.segment_contracts import (
    CompletionAssessment,
    CompletionResult,
    EndingProposal,
    GuardResult,
    GuardViolation,
    PacingEnvelope,
    ScenePlan,
    SegmentDraft,
    SegmentPlan,
    ThreadOperation,
)
from src.story.state import StoryPhase

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _valid_scene_plan() -> ScenePlan:
    return ScenePlan(
        scene_id="scene_01",
        summary="Alice confronts the protagonist.",
        location_id="cafe",
        present_character_ids=("alice",),
        terminal="continue",
    )


def _valid_segment_plan() -> SegmentPlan:
    return SegmentPlan(
        segment_id="seg_01",
        scenes=(_valid_scene_plan(),),
        terminal="decision",
    )


def _valid_pacing_envelope() -> PacingEnvelope:
    return PacingEnvelope(
        phase=StoryPhase.EXPLORATION,
        scene_count=5,
        min_scenes=8,
        max_scenes=20,
        reserved_resolution_scenes=3,
        remaining_budget=15,
        can_end=False,
        must_end=False,
        in_convergence=False,
        max_new_threads=2,
        quiet_scene_allowance=1,
        target_block_range=(8, 25),
    )


def _valid_segment_draft() -> SegmentDraft:
    return SegmentDraft(
        segment_id="seg_01",
        scene_drafts=(
            SceneDraft(
                scene_id="scene_01",
                blocks=(NarrativeBlock(kind="narration", text="The cafe hummed."),),
            ),
        ),
    )


def _make_decision_scene_plan(scene_id="scene_dec") -> ScenePlan:
    return ScenePlan(
        scene_id=scene_id,
        summary="A decision scene",
        location_id="cafe",
        present_character_ids=("alice",),
        terminal="decision",
        decision_id="dec_01",
        choices=(
            ChoicePlan(option_id="opt_1", action_id="act_1", intent="Go left"),
            ChoicePlan(option_id="opt_2", action_id="act_2", intent="Go right"),
        ),
    )


def _make_ending_scene_plan(scene_id="scene_end") -> ScenePlan:
    return ScenePlan(
        scene_id=scene_id,
        summary="An ending scene",
        location_id="cafe",
        present_character_ids=("alice",),
        terminal="ending",
    )


# ---------------------------------------------------------------------------
# SegmentPlan model-level validation (Plan 3 Task 1)
# ---------------------------------------------------------------------------


def test_segment_plan_requires_at_least_one_scene():
    with pytest.raises(ValueError, match="at least 1 scene"):
        SegmentPlan(segment_id="seg_01", scenes=(), terminal="decision")


def test_segment_plan_only_last_scene_can_terminal():
    with pytest.raises(ValidationError, match="non-last scene"):
        SegmentPlan(
            segment_id="seg_01",
            scenes=(
                ScenePlan(
                    scene_id="s1",
                    summary="mid",
                    location_id="cafe",
                    present_character_ids=("alice",),
                    terminal="decision",
                    decision_id="dec_mid",
                    choices=(
                        ChoicePlan(option_id="opt_a", action_id="ask", intent="ask"),
                        ChoicePlan(option_id="opt_b", action_id="observe", intent="watch"),
                    ),
                ),
                ScenePlan(
                    scene_id="s2",
                    summary="last",
                    location_id="cafe",
                    present_character_ids=("alice",),
                    terminal="continue",
                ),
            ),
            terminal="decision",
        )


def test_segment_plan_decision_requires_choices_on_last_scene():
    plan = SegmentPlan(
        segment_id="seg_01",
        scenes=(
            ScenePlan(
                scene_id="s1",
                summary="first",
                location_id="cafe",
                present_character_ids=("alice",),
                terminal="continue",
            ),
            ScenePlan(
                scene_id="s2",
                summary="terminal",
                location_id="cafe",
                present_character_ids=("alice",),
                terminal="decision",
                decision_id="dec_01",
                choices=(
                    ChoicePlan(option_id="a", action_id="ask", intent="ask"),
                    ChoicePlan(option_id="b", action_id="observe", intent="watch"),
                ),
            ),
        ),
        terminal="decision",
    )
    assert plan.terminal == "decision"


def test_segment_plan_ending_requires_ending_proposal():
    plan = SegmentPlan(
        segment_id="seg_02",
        scenes=(_valid_scene_plan(),),
        terminal="ending",
        ending_proposal=EndingProposal(
            title="Farewell",
            tone="bittersweet",
            terminal_state_summary="Alice and Ren part ways.",
        ),
    )
    assert plan.ending_proposal is not None


def test_segment_plan_ending_without_proposal_raises():
    with pytest.raises(ValueError, match="ending_proposal"):
        SegmentPlan(
            segment_id="seg_02",
            scenes=(_valid_scene_plan(),),
            terminal="ending",
        )


# ---------------------------------------------------------------------------
# Guard
# ---------------------------------------------------------------------------


def test_guard_violation_has_required_fields():
    v = GuardViolation(
        kind="knowledge_leak",
        block_index=3,
        character_id="alice",
        detail="Alice references bob_secret without having learned it.",
    )
    assert v.kind == "knowledge_leak"
    assert v.block_index == 3


def test_guard_result_passed():
    r = GuardResult(passed=True)
    assert r.passed is True
    assert r.violations == ()


def test_guard_result_with_violations():
    result = GuardResult(
        passed=False,
        violations=(
            GuardViolation(
                kind="knowledge_leak",
                block_index=2,
                character_id="alice",
                detail="Alice reveals a secret she does not know.",
            ),
        ),
    )
    assert result.passed is False
    assert len(result.violations) == 1
    assert result.violations[0].kind == "knowledge_leak"


# ---------------------------------------------------------------------------
# Agent SDK output schemas (Plan 3 Task 1)
# ---------------------------------------------------------------------------


def test_director_output_strict_schema():
    assert AgentOutputSchema(DirectorOutput).is_strict_json_schema() is True


def test_segment_writer_output_strict_schema():
    assert AgentOutputSchema(SegmentWriterOutput).is_strict_json_schema() is True


def test_provider_schemas_have_no_bare_refs_in_anyof():
    from src.story.runtime.model import ProviderStrictOutputSchema

    def _find_bare_refs(schema):
        bad = []
        if isinstance(schema, dict):
            any_of = schema.get("anyOf")
            if isinstance(any_of, list):
                missing = [b for b in any_of if "type" not in b and "$ref" in b]
                if missing:
                    bad.append(missing)
            for v in schema.values():
                bad.extend(_find_bare_refs(v))
        elif isinstance(schema, list):
            for item in schema:
                bad.extend(_find_bare_refs(item))
        return bad

    assert _find_bare_refs(ProviderStrictOutputSchema(DirectorOutput)._output_schema) == []
    assert _find_bare_refs(ProviderStrictOutputSchema(SegmentWriterOutput)._output_schema) == []


# ---------------------------------------------------------------------------
# Segment contract types (Plan 2, retained)
# ---------------------------------------------------------------------------


def test_ending_proposal():
    proposal = EndingProposal(
        title="The Long Goodbye",
        tone="bittersweet",
        terminal_state_summary="Alice left the city.",
    )
    assert proposal.title == "The Long Goodbye"


def test_thread_operation_open():
    op = ThreadOperation(
        kind="open",
        thread_id="thread_mystery",
        thread_type="mystery",
        involved_character_ids=("alice",),
    )
    assert op.kind == "open"
    assert op.thread_type == "mystery"


def test_thread_operation_advance():
    op = ThreadOperation(
        kind="advance",
        thread_id="thread_mystery",
        urgency=0.8,
    )
    assert op.kind == "advance"


def test_thread_operation_close():
    op = ThreadOperation(
        kind="close",
        thread_id="thread_mystery",
        close_status="resolved",
    )
    assert op.close_status == "resolved"


def test_pacing_envelope():
    env = _valid_pacing_envelope()
    assert env.phase == StoryPhase.EXPLORATION
    assert env.can_end is False


def test_segment_plan_decision():
    plan = SegmentPlan(
        segment_id="seg_01",
        scenes=(_make_decision_scene_plan(),),
        terminal="decision",
    )
    assert plan.segment_id == "seg_01"
    assert plan.terminal == "decision"
    assert plan.ending_proposal is None


def test_segment_plan_ending():
    plan = SegmentPlan(
        segment_id="seg_02",
        scenes=(_make_ending_scene_plan(),),
        terminal="ending",
        ending_proposal=EndingProposal(
            title="Finale",
            tone="epic",
            terminal_state_summary="The end.",
        ),
    )
    assert plan.ending_proposal is not None
    assert plan.ending_proposal.title == "Finale"


def test_segment_plan_ending_with_continue_last_scene_allowed():
    """Model allows construction; validate_segment_plan enforces terminal consistency."""
    plan = SegmentPlan(
        segment_id="seg_04",
        scenes=(_valid_scene_plan(),),
        terminal="ending",
        ending_proposal=EndingProposal(
            title="Finale",
            tone="epic",
            terminal_state_summary="The end.",
        ),
    )
    assert plan.scenes[-1].terminal == "continue"


def test_segment_plan_decision_with_continue_last_scene_allowed():
    """Model allows construction; validate_segment_plan enforces terminal consistency."""
    plan = SegmentPlan(
        segment_id="seg_05",
        scenes=(_valid_scene_plan(),),
        terminal="decision",
    )
    assert plan.scenes[-1].terminal == "continue"


def test_segment_draft():
    draft = _valid_segment_draft()
    assert draft.segment_id == "seg_01"
    assert draft.ending is None


def test_segment_draft_with_ending():
    draft = SegmentDraft(
        segment_id="seg_02",
        scene_drafts=(
            SceneDraft(
                scene_id="scene_01",
                blocks=(NarrativeBlock(kind="narration", text="Text."),),
            ),
        ),
        ending=EndingDraft(
            ending_id="ending_001",
            title="Finale",
            blocks=(NarrativeBlock(kind="narration", text="The end."),),
            tone="epic",
            terminal_state_summary="World saved.",
        ),
    )
    assert draft.ending is not None
    assert draft.ending.tone == "epic"


def test_completion_assessment():
    a = CompletionAssessment(
        requirement_id="req_a",
        satisfied=True,
        cited_event_ids=("evt-1",),
        rationale="Fact committed",
    )
    assert a.satisfied is True


def test_completion_result():
    result = CompletionResult(
        assessments=(
            CompletionAssessment(requirement_id="req_a", satisfied=True, rationale="ok"),
            CompletionAssessment(requirement_id="req_b", satisfied=False, rationale="no"),
        ),
        cleared=False,
    )
    assert len(result.assessments) == 2
    assert result.cleared is False


# ---------------------------------------------------------------------------
# Fake-based integration smoke tests
# ---------------------------------------------------------------------------


def test_fake_director_plan_segment():
    import asyncio

    from src.story.runtime.pacing import compute_pacing_envelope
    from src.story.script_pack.compiler import compile_source
    from src.story.state import initial_session_state
    from tests.fakes import FakeDirector, budget_test_pack_dict

    pack = compile_source(budget_test_pack_dict())
    state = initial_session_state(pack, "s1", session_seed=1)
    pacing = compute_pacing_envelope(state, pack)
    director = FakeDirector()
    plan = asyncio.run(director.plan_segment(pack, state, pacing))
    assert plan.segment_id is not None
    assert len(plan.scenes) >= 1
    assert plan.terminal in ("decision", "ending")


def test_fake_segment_writer_write_segment():
    import asyncio

    from src.story.runtime.pacing import compute_pacing_envelope
    from src.story.script_pack.compiler import compile_source
    from src.story.state import initial_session_state
    from tests.fakes import FakeDirector, FakeSegmentWriter, budget_test_pack_dict

    pack = compile_source(budget_test_pack_dict())
    state = initial_session_state(pack, "s1", session_seed=1)
    pacing = compute_pacing_envelope(state, pack)
    director = FakeDirector()
    plan = asyncio.run(director.plan_segment(pack, state, pacing))
    writer = FakeSegmentWriter()
    draft = asyncio.run(writer.write_segment(pack, state, plan))
    assert draft.segment_id == plan.segment_id
    assert len(draft.scene_drafts) == len(plan.scenes)


def test_fake_guard_passes():
    import asyncio

    from src.story.runtime.pacing import compute_pacing_envelope
    from src.story.script_pack.compiler import compile_source
    from src.story.state import initial_session_state
    from tests.fakes import (
        FakeDirector,
        FakeGuard,
        FakeSegmentWriter,
        budget_test_pack_dict,
    )

    pack = compile_source(budget_test_pack_dict())
    state = initial_session_state(pack, "s1", session_seed=1)
    pacing = compute_pacing_envelope(state, pack)
    director = FakeDirector()
    plan = asyncio.run(director.plan_segment(pack, state, pacing))
    writer = FakeSegmentWriter()
    draft = asyncio.run(writer.write_segment(pack, state, plan))
    guard = FakeGuard()
    result = guard.check_segment(pack, state, plan, draft)
    assert result.passed is True
