import pytest
from pydantic import ValidationError

from src.story.runtime.contracts import ChoicePlan, PlannerOutput, ScenePlan


def test_scene_plan_requires_two_choices_for_decision():
    with pytest.raises(ValidationError, match="choices"):
        ScenePlan(
            scene_id="scene_01",
            summary="Alice confronts the protagonist.",
            location_id="cafe",
            present_character_ids=("alice",),
            terminal="decision",
            decision_id="decision_01",
            choices=(ChoicePlan(option_id="ask", action_id="ask", intent="ask"),),
        )


def test_planner_output_is_discriminated():
    plan = ScenePlan(
        scene_id="scene_01",
        summary="The protagonist takes in the cafe.",
        location_id="cafe",
        present_character_ids=("alice",),
        terminal="continue",
    )
    output = PlannerOutput(kind="scene", scene=plan)
    assert output.scene is not None
    assert output.resolution is None
