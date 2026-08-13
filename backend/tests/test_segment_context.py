from __future__ import annotations

import pytest

from src.story.runtime.contracts import (
    ChoicePlan,
    EndingProposal,
    PacingEnvelope,
    ScenePlan,
    SegmentPlan,
)
from src.story.runtime.segment_context import (
    build_director_context,
    build_segment_writer_context,
)
from src.story.script_pack import compile_source
from src.story.state import StoryPhase, initial_session_state
from tests.story_factories import minimal_script_pack_dict


@pytest.fixture
def pack():
    return compile_source(minimal_script_pack_dict())


@pytest.fixture
def state(pack):
    return initial_session_state(pack, "session_01", session_seed=42)


@pytest.fixture
def pacing():
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


def test_director_context_includes_world_truth_and_pacing(pack, state, pacing):
    ctx = build_director_context(pack, state, pacing)
    assert "world_truth" in ctx
    assert "pacing" in ctx
    assert ctx["pacing"]["phase"] == "exploration"
    assert "completion_requirements" in ctx or "goals" in ctx
    assert "open_threads" in ctx


def test_director_context_does_not_leak_character_secrets(pack, state, pacing):
    ctx = build_director_context(pack, state, pacing)
    # Director gets fact summaries but not raw secret dumps as shared text
    for char in ctx.get("characters", []):
        # Director sees knowledge references, not other characters' secrets
        assert "secrets" not in char or char.get("secrets") == []


def test_writer_context_per_character_knowledge_is_scoped(pack, state):
    plan = SegmentPlan(
        segment_id="seg_01",
        scenes=(
            ScenePlan(
                scene_id="scene_01",
                summary="Alice talks with protagonist.",
                location_id="cafe",
                present_character_ids=("alice",),
                terminal="decision",
                decision_id="dec_01",
                choices=(
                    ChoicePlan(option_id="opt_a", action_id="ask", intent="ask directly"),
                    ChoicePlan(option_id="opt_b", action_id="observe", intent="watch carefully"),
                ),
            ),
        ),
        terminal="decision",
    )
    ctx = build_segment_writer_context(pack, state, plan)
    assert "characters" in ctx
    for char in ctx["characters"]:
        # Each character only sees their OWN known facts, not other characters' knowledge
        assert "known_facts" in char
        # Should NOT have access to a different character's secrets
        assert "other_characters_secrets" not in char
    # Writer should receive the approved plan
    assert "approved_plan" in ctx
    assert ctx["approved_plan"]["segment_id"] == "seg_01"


def test_writer_context_includes_ending_proposal_for_ending_segments(pack, state):
    plan = SegmentPlan(
        segment_id="seg_02",
        scenes=(
            ScenePlan(
                scene_id="scene_final",
                summary="The story concludes.",
                location_id="cafe",
                present_character_ids=("alice",),
                terminal="ending",
            ),
        ),
        terminal="ending",
        ending_proposal=EndingProposal(
            title="Farewell, Cafe",
            tone="bittersweet",
            terminal_state_summary="They part ways at the cafe.",
        ),
    )
    ctx = build_segment_writer_context(pack, state, plan)
    assert ctx.get("ending_proposal") is not None
    assert ctx["ending_proposal"]["title"] == "Farewell, Cafe"


def test_writer_context_includes_world_rules(pack, state):
    plan = SegmentPlan(
        segment_id="seg_01",
        scenes=(
            ScenePlan(
                scene_id="scene_01",
                summary="A quiet moment.",
                location_id="cafe",
                present_character_ids=("alice",),
                terminal="continue",
            ),
        ),
        terminal="decision",
    )
    ctx = build_segment_writer_context(pack, state, plan)
    assert "world_rules" in ctx
    assert "premise" in ctx["world_rules"]
