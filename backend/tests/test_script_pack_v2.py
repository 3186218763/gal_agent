"""TDD tests for Script Pack v2.0 schema models."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from src.story.script_pack.models import (
    EvidenceHintsSource,
    OpeningStateSource,
    StoryHistorySource,
    WorldSettingSource,
)


class TestEvidenceHintsSource:
    def test_default_empty_hints(self):
        hints = EvidenceHintsSource()
        assert hints.fact_ids == ()
        assert hints.goal_ids == ()

    def test_with_fact_and_goal_refs(self):
        hints = EvidenceHintsSource(
            fact_ids=("core_cause",),
            goal_ids=("protagonist_understand",),
        )
        assert hints.fact_ids == ("core_cause",)
        assert hints.goal_ids == ("protagonist_understand",)

    def test_rejects_non_safe_id(self):
        with pytest.raises(ValidationError):
            EvidenceHintsSource(fact_ids=("BadID",))


class TestWorldSettingSource:
    def test_minimal_with_premise_and_locations(self):
        ws = WorldSettingSource(
            premise="A quiet cafe hides a secret.",
            locations=[{"id": "cafe", "name": "Cafe"}],
        )
        assert ws.premise == "A quiet cafe hides a secret."
        assert ws.forbidden_content == ()
        assert ws.fact_rules == ()
        assert len(ws.locations) == 1

    def test_full_fields(self):
        ws = WorldSettingSource(
            premise="A quiet cafe hides a secret.",
            immutable_rules=["Death is irreversible."],
            locations=[{"id": "cafe", "name": "Cafe"}],
            factions=[{"id": "veiled_circle", "name": "Veiled Circle"}],
            forbidden_content=["explicit violence"],
            fact_rules=["No supernatural powers."],
        )
        assert ws.immutable_rules == ("Death is irreversible.",)
        assert ws.forbidden_content == ("explicit violence",)

    def test_rejects_empty_premise(self):
        with pytest.raises(ValidationError):
            WorldSettingSource(premise="", locations=[{"id": "cafe", "name": "Cafe"}])

    def test_rejects_empty_locations(self):
        with pytest.raises(ValidationError):
            WorldSettingSource(premise="ok", locations=())


class TestStoryHistorySource:
    def test_minimal_summary_only(self):
        hist = StoryHistorySource(summary="Before the game started, nothing happened.")
        assert hist.events == ()

    def test_with_events(self):
        hist = StoryHistorySource(
            summary="A notebook was lost.",
            events=[
                {
                    "summary": "Alice lost her notebook.",
                    "participants": ("alice",),
                },
                {
                    "summary": "Bob saw something suspicious.",
                    "participants": ("bob", "alice"),
                    "remembered_differently_by": {"bob": "He saw a stranger."},
                },
            ],
        )
        assert len(hist.events) == 2
        assert hist.events[1].remembered_differently_by["bob"] == "He saw a stranger."

    def test_rejects_empty_summary(self):
        with pytest.raises(ValidationError):
            StoryHistorySource(summary="")


class TestOpeningStateSource:
    def test_minimal_with_location(self):
        os = OpeningStateSource(location="cafe")
        assert os.location == "cafe"
        assert os.present_characters == ()
        assert os.known_facts == ()
        assert os.time_label == "opening"
        assert os.starting_pressure == 0.1

    def test_full_fields(self):
        os = OpeningStateSource(
            location="cafe",
            present_characters=("alice", "bob"),
            known_facts=("cafe_is_open",),
            time_label="Saturday afternoon",
            starting_pressure=0.3,
        )
        assert os.starting_pressure == 0.3

    def test_pressure_out_of_range(self):
        with pytest.raises(ValidationError):
            OpeningStateSource(location="cafe", starting_pressure=1.5)


# ---------------------------------------------------------------------------
# Task 2: ScriptPackSource v1.0/v2.0 discriminated union
# ---------------------------------------------------------------------------

from src.story.script_pack.models import ScriptPackSource


def _minimal_v2_raw():
    """Return a minimal valid v2.0 pack dict (no endings, has completion_requirements)."""
    return {
        "schema_version": "2.0",
        "identity": {
            "id": "test_pack_v2",
            "title": "Test Pack V2",
            "language": "en",
            "genres": ["mystery"],
            "expected_minutes": 60,
        },
        "experience": {
            "viewpoint": "first_person",
            "prose_style": "concise",
            "tone": "quiet mystery",
            "min_scenes": 8,
            "max_scenes": 20,
        },
        "protagonist": {
            "id": "protagonist",
            "name": "Ren",
            "personality": {"traits": ["observant"]},
            "background": "A new student.",
            "capabilities": ["ask", "observe"],
        },
        "world_setting": {
            "premise": "A notebook disappeared.",
            "locations": [{"id": "cafe", "name": "Cafe"}],
        },
        "story_history": {
            "summary": "Nothing happened before the opening.",
        },
        "opening_state": {
            "location": "cafe",
            "present_characters": ["alice"],
            "known_facts": ["cafe_is_open"],
        },
        "characters": [
            {
                "id": "alice",
                "name": "Alice",
                "public_profile": "An outgoing student.",
                "personality": {"traits": ["outgoing"]},
                "voice": {"style": "direct"},
                "drives": ["find an ally"],
                "knowledge": ["cafe_is_open"],
            }
        ],
        "facts": {
            "fixed": [
                {
                    "id": "cafe_is_open",
                    "statement": "The cafe is open.",
                    "known_by": ["alice"],
                    "visibility": "revealed",
                }
            ],
        },
        "goals": [
            {
                "id": "alice_find_ally",
                "owner": "alice",
                "desire": "Find an ally.",
                "urgency": 0.7,
                "success_condition": "relationships.alice.trust >= 70",
                "failure_condition": "relationships.alice.trust <= 10",
            }
        ],
        "completion_requirements": [
            {
                "id": "understand_truth",
                "description": "Player must understand the core truth.",
            }
        ],
        "interaction_rules": {
            "enabled_standard": ["ask", "observe"],
        },
        "assets": {},
    }


class TestScriptPackSourceV2:
    def test_valid_v2_pack_accepted(self):
        source = ScriptPackSource.model_validate(_minimal_v2_raw())
        assert source.schema_version == "2.0"
        assert source.completion_requirements[0].id == "understand_truth"

    def test_v2_rejects_endings_field(self):
        raw = _minimal_v2_raw()
        raw["endings"] = [
            {
                "id": "bad_ending",
                "title": "Bad",
                "type": "fallback",
                "priority": 1,
                "eligibility": {"all": ["session.scene_count >= 99"]},
                "required_outcomes": ["something"],
                "closing_tone": "quiet",
            }
        ]
        with pytest.raises(ValidationError, match="endings"):
            ScriptPackSource.model_validate(raw)

    def test_v2_requires_completion_requirements(self):
        raw = _minimal_v2_raw()
        del raw["completion_requirements"]
        with pytest.raises(ValidationError, match="completion_requirements"):
            ScriptPackSource.model_validate(raw)

    def test_v2_requires_world_setting(self):
        raw = _minimal_v2_raw()
        del raw["world_setting"]
        with pytest.raises(ValidationError, match="world_setting"):
            ScriptPackSource.model_validate(raw)

    def test_v2_requires_story_history(self):
        raw = _minimal_v2_raw()
        del raw["story_history"]
        with pytest.raises(ValidationError, match="story_history"):
            ScriptPackSource.model_validate(raw)

    def test_v2_requires_opening_state(self):
        raw = _minimal_v2_raw()
        del raw["opening_state"]
        with pytest.raises(ValidationError, match="opening_state"):
            ScriptPackSource.model_validate(raw)

    def test_v2_rejects_world_field(self):
        raw = _minimal_v2_raw()
        raw["world"] = {
            "premise": "test",
            "locations": [{"id": "cafe", "name": "Cafe"}],
            "initial_situation": {"location": "cafe"},
        }
        with pytest.raises(ValidationError):
            ScriptPackSource.model_validate(raw)

    def test_v1_pack_still_accepted_unchanged(self):
        from tests.story_factories import minimal_script_pack_dict

        source = ScriptPackSource.model_validate(minimal_script_pack_dict())
        assert source.schema_version == "1.0"
