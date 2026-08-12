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
