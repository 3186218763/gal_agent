from __future__ import annotations

import pytest

from src.story.runtime.contracts import (
    ChoicePlan,
    FactCommitPlan,
    SceneDraft,
    ScenePlan,
    WrittenChoice,
)
from src.story.runtime.guard import Guard
from src.story.runtime.segment_contracts import (
    EndingProposal,
    SegmentDraft,
    SegmentPlan,
)
from src.story.script_pack import compile_source
from src.story.state import NarrativeBlock, initial_session_state
from tests.story_factories import minimal_script_pack_dict


@pytest.fixture
def pack():
    return compile_source(minimal_script_pack_dict())


@pytest.fixture
def state(pack):
    return initial_session_state(pack, "session_01", session_seed=42)


@pytest.fixture
def guard():
    return Guard()


def _decision_plan() -> SegmentPlan:
    return SegmentPlan(
        segment_id="seg_01",
        scenes=(
            ScenePlan(
                scene_id="scene_01",
                summary="Alice waits.",
                location_id="cafe",
                present_character_ids=("alice",),
                terminal="continue",
            ),
            ScenePlan(
                scene_id="scene_02",
                summary="Decision point.",
                location_id="cafe",
                present_character_ids=("alice",),
                terminal="decision",
                decision_id="dec_01",
                choices=(
                    ChoicePlan(option_id="opt_a", action_id="ask", intent="ask"),
                    ChoicePlan(option_id="opt_b", action_id="observe", intent="observe"),
                ),
            ),
        ),
        terminal="decision",
    )


def _matching_draft() -> SegmentDraft:
    return SegmentDraft(
        segment_id="seg_01",
        scene_drafts=(
            SceneDraft(
                scene_id="scene_01",
                blocks=(NarrativeBlock(kind="narration", text="The cafe hummed."),),
            ),
            SceneDraft(
                scene_id="scene_02",
                blocks=(
                    NarrativeBlock(kind="narration", text="Alice looked up."),
                    NarrativeBlock(kind="dialogue", character_id="alice", text="What now?"),
                ),
                choices=(
                    WrittenChoice(option_id="opt_a", label="Ask her"),
                    WrittenChoice(option_id="opt_b", label="Observe"),
                ),
            ),
        ),
        choices=(
            WrittenChoice(option_id="opt_a", label="Ask her"),
            WrittenChoice(option_id="opt_b", label="Observe"),
        ),
    )


def test_guard_passes_valid_segment(guard, pack, state):
    plan = _decision_plan()
    draft = _matching_draft()
    result = guard.check_segment(pack, state, plan, draft)
    assert result.passed is True
    assert result.violations == ()


def test_guard_rejects_segment_id_mismatch(guard, pack, state):
    plan = _decision_plan()
    draft = _matching_draft()
    draft = draft.model_copy(update={"segment_id": "wrong_seg"})
    result = guard.check_segment(pack, state, plan, draft)
    assert result.passed is False
    assert any("segment_id" in v.detail.lower() for v in result.violations)


def test_guard_rejects_wrong_speaker(guard, pack, state):
    plan = _decision_plan()
    draft = _matching_draft()
    # Add a dialogue block for a character not present
    bad_blocks = draft.scene_drafts[1].blocks + (
        NarrativeBlock(kind="dialogue", character_id="unknown_char", text="Hello."),
    )
    draft = draft.model_copy(
        update={
            "scene_drafts": (
                draft.scene_drafts[0],
                draft.scene_drafts[1].model_copy(update={"blocks": bad_blocks}),
            )
        }
    )
    result = guard.check_segment(pack, state, plan, draft)
    assert result.passed is False
    assert any(v.kind == "wrong_speaker" for v in result.violations)


def test_guard_rejects_scene_id_mismatch(guard, pack, state):
    plan = _decision_plan()
    draft = _matching_draft()
    draft = draft.model_copy(
        update={
            "scene_drafts": (
                draft.scene_drafts[0].model_copy(update={"scene_id": "wrong_id"}),
                draft.scene_drafts[1],
            )
        }
    )
    result = guard.check_segment(pack, state, plan, draft)
    assert result.passed is False
    assert any("scene_id" in v.detail.lower() for v in result.violations)


def test_guard_rejects_choice_id_mismatch(guard, pack, state):
    plan = _decision_plan()
    draft = _matching_draft()
    draft = draft.model_copy(
        update={
            "choices": (
                WrittenChoice(option_id="opt_a", label="Ask"),
                WrittenChoice(option_id="wrong_id", label="Something else"),
            )
        }
    )
    result = guard.check_segment(pack, state, plan, draft)
    assert result.passed is False
    assert any("choice" in v.detail.lower() for v in result.violations)


def test_guard_rejects_empty_blocks(guard, pack, state):
    plan = _decision_plan()
    draft = _matching_draft()
    draft = draft.model_copy(
        update={
            "scene_drafts": (
                draft.scene_drafts[0].model_copy(update={"blocks": ()}),
                draft.scene_drafts[1],
            )
        }
    )
    result = guard.check_segment(pack, state, plan, draft)
    assert result.passed is False


def test_guard_rejects_duplicate_choice_labels(guard, pack, state):
    plan = _decision_plan()
    draft = _matching_draft()
    draft = draft.model_copy(
        update={
            "choices": (
                WrittenChoice(option_id="opt_a", label="Same"),
                WrittenChoice(option_id="opt_b", label="Same"),
            )
        }
    )
    result = guard.check_segment(pack, state, plan, draft)
    assert result.passed is False


def test_guard_rejects_dialogue_from_absent_character(guard, pack, state):
    plan = _decision_plan()
    draft = _matching_draft()
    # Add a dialogue block from a character who is not present in the scene
    bad_blocks = draft.scene_drafts[1].blocks + (
        NarrativeBlock(kind="dialogue", character_id="non_present_char", text="Hello."),
    )
    draft = draft.model_copy(
        update={
            "scene_drafts": (
                draft.scene_drafts[0],
                draft.scene_drafts[1].model_copy(update={"blocks": bad_blocks}),
            )
        }
    )
    result = guard.check_segment(pack, state, plan, draft)
    assert result.passed is False
    assert any(v.kind == "wrong_speaker" for v in result.violations)


def test_guard_is_structural_only_for_semantic_conflicts(guard, pack, state):
    """Natural-language knowledge leaks and rule contradictions are the
    Semantic Judge's job (it sees the prose window); the guard no longer
    keyword-matches — a fact-id literal in dialogue is already a rules
    violation caught upstream, so this content must pass the guard."""
    plan = _decision_plan()
    plan = plan.model_copy(
        update={
            "scenes": (
                plan.scenes[0],
                plan.scenes[1].model_copy(update={"related_fact_ids": ("who_took_notebook",)}),
            )
        }
    )
    draft = _matching_draft()
    leaky_blocks = draft.scene_drafts[1].blocks + (
        NarrativeBlock(
            kind="dialogue",
            character_id="alice",
            text="Dead characters cannot return. Actually, they can! who_took_notebook!",
        ),
    )
    draft = draft.model_copy(
        update={
            "scene_drafts": (
                draft.scene_drafts[0],
                draft.scene_drafts[1].model_copy(update={"blocks": leaky_blocks}),
            )
        }
    )
    result = guard.check_segment(pack, state, plan, draft)
    assert result.passed is True


def test_guard_scene_count_matches_plan(guard, pack, state):
    plan = _decision_plan()
    draft = _matching_draft()
    # Remove a scene draft — should fail
    draft = draft.model_copy(update={"scene_drafts": (draft.scene_drafts[0],)})
    result = guard.check_segment(pack, state, plan, draft)
    assert result.passed is False
    assert any(
        "scene" in v.detail.lower() and "count" in v.detail.lower() for v in result.violations
    )


def test_guard_does_not_mutate_state(guard, pack, state):
    plan = _decision_plan()
    draft = _matching_draft()
    original_revision = state.revision
    original_facts = dict(state.facts)
    guard.check_segment(pack, state, plan, draft)
    assert state.revision == original_revision
    assert state.facts == original_facts


def test_guard_rejects_choices_on_ending_terminal(guard, pack, state):
    plan = SegmentPlan(
        segment_id="seg_ending",
        scenes=(
            ScenePlan(
                scene_id="scene_01",
                summary="The end.",
                location_id="cafe",
                present_character_ids=("alice",),
                terminal="ending",
            ),
        ),
        terminal="ending",
        ending_proposal=EndingProposal(
            title="Together",
            tone="hopeful",
            terminal_state_summary="Alice and Ren leave the cafe.",
        ),
    )
    # Draft must not carry choices for a non-decision terminal
    draft = SegmentDraft(
        segment_id="seg_ending",
        scene_drafts=(
            SceneDraft(
                scene_id="scene_01",
                blocks=(NarrativeBlock(kind="narration", text="The curtain falls."),),
            ),
        ),
        choices=(WrittenChoice(option_id="opt_x", label="Fabricated"),),
    )
    result = guard.check_segment(pack, state, plan, draft)
    assert result.passed is False
    assert any(v.kind == "unauthorized_fact" for v in result.violations)
    assert any("does not allow choices" in v.detail for v in result.violations)


def test_guard_rejects_choices_on_continue_terminal(guard, pack, state):
    # Check 6b: for a decision plan, a non-last scene draft must not carry choices.
    plan = _decision_plan()
    draft = _matching_draft()
    draft = draft.model_copy(
        update={
            "scene_drafts": (
                draft.scene_drafts[0].model_copy(
                    update={
                        "choices": (WrittenChoice(option_id="opt_x", label="Fabricated"),),
                    }
                ),
                draft.scene_drafts[1],
            )
        }
    )
    result = guard.check_segment(pack, state, plan, draft)
    assert result.passed is False
    assert any(v.kind == "unauthorized_fact" for v in result.violations)


def test_guard_rejects_ending_draft_without_ending(guard, pack, state):
    plan = SegmentPlan(
        segment_id="seg_ending",
        scenes=(
            ScenePlan(
                scene_id="scene_01",
                summary="The end.",
                location_id="cafe",
                present_character_ids=("alice",),
                terminal="ending",
            ),
        ),
        terminal="ending",
        ending_proposal=EndingProposal(
            title="Together",
            tone="hopeful",
            terminal_state_summary="Alice and Ren leave the cafe.",
        ),
    )
    # Ending terminal requires an EndingDraft — omit it
    draft = SegmentDraft(
        segment_id="seg_ending",
        scene_drafts=(
            SceneDraft(
                scene_id="scene_01",
                blocks=(NarrativeBlock(kind="narration", text="The curtain falls."),),
            ),
        ),
        ending=None,
    )
    result = guard.check_segment(pack, state, plan, draft)
    assert result.passed is False
    assert any("ending" in v.detail for v in result.violations)


def test_guard_rejects_fact_commit_without_evidence(guard, pack, state):
    # "who_took_notebook" is a latent question requiring 1 evidence event.
    # A fact_commit on it without any committed evidence must be rejected.
    plan = SegmentPlan(
        segment_id="seg_commit",
        scenes=(
            ScenePlan(
                scene_id="scene_01",
                summary="Revelation.",
                location_id="cafe",
                present_character_ids=("alice",),
                terminal="continue",
                fact_commits=(
                    FactCommitPlan(
                        fact_id="who_took_notebook",
                        value="stranger",
                        reason="first_irreversible_evidence",
                    ),
                ),
            ),
            ScenePlan(
                scene_id="scene_02",
                summary="Decision.",
                location_id="cafe",
                present_character_ids=("alice",),
                terminal="decision",
                decision_id="dec_01",
                choices=(
                    ChoicePlan(option_id="opt_a", action_id="ask", intent="ask"),
                    ChoicePlan(option_id="opt_b", action_id="observe", intent="observe"),
                ),
            ),
        ),
        terminal="decision",
    )
    draft = SegmentDraft(
        segment_id="seg_commit",
        scene_drafts=(
            SceneDraft(
                scene_id="scene_01",
                blocks=(NarrativeBlock(kind="narration", text="Revealed."),),
            ),
            SceneDraft(
                scene_id="scene_02",
                blocks=(NarrativeBlock(kind="narration", text="What now?"),),
                choices=(
                    WrittenChoice(option_id="opt_a", label="Ask"),
                    WrittenChoice(option_id="opt_b", label="Observe"),
                ),
            ),
        ),
        choices=(
            WrittenChoice(option_id="opt_a", label="Ask"),
            WrittenChoice(option_id="opt_b", label="Observe"),
        ),
    )
    result = guard.check_segment(pack, state, plan, draft)
    assert result.passed is False
    assert any("evidence" in v.detail for v in result.violations)


def test_guard_does_not_flag_rule_reference_with_ordinary_modals(guard, pack, state):
    """Dialogue restating an immutable rule passes the structural guard;
    semantic contradiction detection is the judge's responsibility."""
    plan = _decision_plan()
    draft = _matching_draft()
    good_blocks = draft.scene_drafts[1].blocks + (
        NarrativeBlock(
            kind="dialogue",
            character_id="alice",
            text="Dead characters cannot return. So we must move on.",
        ),
    )
    draft = draft.model_copy(
        update={
            "scene_drafts": (
                draft.scene_drafts[0],
                draft.scene_drafts[1].model_copy(update={"blocks": good_blocks}),
            )
        }
    )
    result = guard.check_segment(pack, state, plan, draft)
    assert result.passed is True
