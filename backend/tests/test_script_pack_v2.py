"""TDD tests for Script Pack v2.0 schema models."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from src.story.script_pack.models import (
    OpeningStateSource,
    StoryHistorySource,
    WorldSettingSource,
)


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
            },
            {
                "id": "bob",
                "name": "Bob",
                "public_profile": "A cautious researcher.",
                "personality": {"traits": ["cautious"]},
                "voice": {"style": "precise"},
                "drives": ["verify the evidence"],
                "knowledge": ["cafe_is_open"],
            },
        ],
        "facts": {
            "fixed": [
                {
                    "id": "cafe_is_open",
                    "statement": "The cafe is open.",
                    "known_by": ["alice", "bob"],
                    "visibility": "revealed",
                }
            ],
            "latent_questions": [
                {
                    "id": "who_took_notebook",
                    "question": "Who took the notebook?",
                    "candidates": [
                        {"value": "alice", "weight": 1.0},
                        {"value": "stranger", "weight": 1.0},
                    ],
                    "commit_when": ["explicit_revelation"],
                    "evidence_required": 1,
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
                "fact_revealed": {"fact_id": "who_took_notebook"},
            }
        ],
        "conflict_axes": [
            {
                "id": "trust_vs_evidence",
                "values": ["trust", "evidence"],
                "source_character_ids": ["alice", "bob"],
                "initial_incompatibility": (
                    "Alice needs personal trust while Bob requires verifiable evidence."
                ),
            }
        ],
        "relationship_event_tags": [
            {"id": "public_trust", "description": "Trusted someone in public."},
            {"id": "accepted_truth", "description": "Accepted an inconvenient truth."},
        ],
        "relationship_turning_points": [
            {
                "id": "alice_mutual_trust",
                "character_id": "alice",
                "all_of_event_tags": ["public_trust", "accepted_truth"],
                "min_distinct_source_choices": 2,
            }
        ],
        "obligation_kinds": [
            {
                "id": "keep_secret",
                "description": "Keep a disclosed secret.",
                "burden": 2,
                "allowed_outcomes": ["fulfilled", "broken", "released"],
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
        assert source.completion_requirements[0].fact_revealed.fact_id == "who_took_notebook"

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


# ---------------------------------------------------------------------------
# Task 3: Compiler v2.0 branch
# ---------------------------------------------------------------------------

from src.story.script_pack.compiler import PackCompileError, compile_source


class TestCompileV2:
    def test_v2_compiles_and_sets_completion_requirement_ids(self):
        compiled = compile_source(_minimal_v2_raw())
        assert compiled.completion_requirement_ids == frozenset({"understand_truth"})
        assert compiled.ending_ids == frozenset()

    def test_v2_hash_is_stable(self):
        first = compile_source(_minimal_v2_raw())
        second = compile_source(_minimal_v2_raw())
        assert first.pack_hash == second.pack_hash

    def test_v2_rejects_duplicate_completion_requirement_ids(self):
        raw = _minimal_v2_raw()
        raw["completion_requirements"].append(dict(raw["completion_requirements"][0]))
        with pytest.raises(PackCompileError, match="duplicate completion_requirement id"):
            compile_source(raw)

    def test_v2_rejects_unknown_fact_in_completion_evidence(self):
        raw = _minimal_v2_raw()
        raw["completion_requirements"][0]["fact_revealed"] = {
            "fact_id": "nonexistent_fact",
        }
        with pytest.raises(PackCompileError, match="nonexistent_fact"):
            compile_source(raw)

    def test_v2_accepts_recursive_completion_evidence(self):
        raw = _minimal_v2_raw()
        raw["completion_requirements"] = [
            {
                "id": "complete_arc",
                "description": "Reveal truth and carry a meaningful consequence.",
                "all": [
                    {"fact_revealed": {"fact_id": "who_took_notebook"}},
                    {
                        "any": [
                            {
                                "relationship_turning_point": {
                                    "turning_point_id": "alice_mutual_trust"
                                }
                            },
                            {"obligation_fulfilled": {"min_burden": 1}},
                            {"cost_incurred": {"min_severity": 1}},
                            {
                                "stance_defended": {
                                    "min_challenges": 1,
                                    "min_cost_severity": 1,
                                }
                            },
                        ]
                    },
                ],
            }
        ]
        compiled = compile_source(raw)
        requirement = compiled.source.completion_requirements[0]
        assert requirement.all[0].fact_revealed.fact_id == "who_took_notebook"
        assert requirement.all[1].any[0].relationship_turning_point.turning_point_id == (
            "alice_mutual_trust"
        )

    @pytest.mark.parametrize("operator", ["all", "any"])
    def test_v2_rejects_empty_completion_group(self, operator):
        raw = _minimal_v2_raw()
        raw["completion_requirements"][0].pop("fact_revealed")
        raw["completion_requirements"][0][operator] = []
        with pytest.raises(PackCompileError, match=f"{operator} group cannot be empty"):
            compile_source(raw)

    def test_v2_rejects_completion_requirement_without_evidence(self):
        raw = _minimal_v2_raw()
        raw["completion_requirements"][0].pop("fact_revealed")
        with pytest.raises(PackCompileError, match="exactly one evidence operator"):
            compile_source(raw)

    def test_v2_rejects_mixed_completion_evidence_node(self):
        raw = _minimal_v2_raw()
        raw["completion_requirements"][0]["cost_incurred"] = {"min_severity": 1}
        with pytest.raises(PackCompileError, match="exactly one evidence operator"):
            compile_source(raw)

    def test_v2_rejects_unknown_turning_point_in_completion_evidence(self):
        raw = _minimal_v2_raw()
        raw["completion_requirements"][0] = {
            "id": "bond",
            "description": "Form a bond.",
            "relationship_turning_point": {"turning_point_id": "missing"},
        }
        with pytest.raises(PackCompileError, match="unknown turning point missing"):
            compile_source(raw)

    def test_v2_rejects_revealed_fixed_fact_as_completion_evidence(self):
        raw = _minimal_v2_raw()
        raw["completion_requirements"][0]["fact_revealed"] = {"fact_id": "cafe_is_open"}
        with pytest.raises(PackCompileError, match="cannot produce a FactRevealed event"):
            compile_source(raw)

    def test_v2_rejects_derived_fact_as_completion_evidence(self):
        raw = _minimal_v2_raw()
        raw["facts"]["derived"] = [
            {"id": "derived_truth", "condition": "relationships.alice.trust >= 1"}
        ]
        raw["completion_requirements"][0]["fact_revealed"] = {"fact_id": "derived_truth"}
        with pytest.raises(PackCompileError, match="cannot produce a FactRevealed event"):
            compile_source(raw)

    def test_v2_accepts_hidden_fixed_fact_as_completion_evidence(self):
        raw = _minimal_v2_raw()
        raw["facts"]["fixed"].append(
            {
                "id": "hidden_warning",
                "statement": "A warning was hidden under the table.",
                "known_by": [],
                "visibility": "hidden",
            }
        )
        raw["completion_requirements"][0]["fact_revealed"] = {"fact_id": "hidden_warning"}
        assert compile_source(raw).source.completion_requirements[0].fact_revealed.fact_id == (
            "hidden_warning"
        )

    def test_v2_rejects_unsatisfiable_obligation_fulfilled_evidence(self):
        raw = _minimal_v2_raw()
        raw["obligation_kinds"] = [
            {
                "id": "break_only",
                "description": "This responsibility can only be broken.",
                "burden": 1,
                "allowed_outcomes": ["broken"],
            }
        ]
        raw["completion_requirements"][0] = {
            "id": "responsibility",
            "description": "Fulfill a heavy responsibility.",
            "obligation_fulfilled": {"min_burden": 3},
        }
        with pytest.raises(PackCompileError, match="no fulfillable obligation kind"):
            compile_source(raw)

    def test_v2_rejects_turning_point_unknown_character(self):
        raw = _minimal_v2_raw()
        raw["relationship_turning_points"][0]["character_id"] = "missing"
        with pytest.raises(PackCompileError, match="unknown character missing"):
            compile_source(raw)

    @pytest.mark.parametrize(
        ("field", "value"),
        [
            ("values", ["trust", "trust"]),
            ("source_character_ids", ["alice", "alice"]),
        ],
    )
    def test_v2_rejects_duplicate_conflict_axis_members(self, field, value):
        raw = _minimal_v2_raw()
        raw["conflict_axes"][0][field] = value
        with pytest.raises(PackCompileError, match="duplicate"):
            compile_source(raw)

    def test_v2_rejects_duplicate_turning_point_tags(self):
        raw = _minimal_v2_raw()
        raw["relationship_turning_points"][0]["all_of_event_tags"] = [
            "public_trust",
            "public_trust",
        ]
        with pytest.raises(PackCompileError, match="duplicate"):
            compile_source(raw)

    def test_v2_rejects_duplicate_obligation_outcomes(self):
        raw = _minimal_v2_raw()
        raw["obligation_kinds"][0]["allowed_outcomes"] = ["fulfilled", "fulfilled"]
        with pytest.raises(PackCompileError, match="duplicate"):
            compile_source(raw)

    @pytest.mark.parametrize(
        "leaf",
        [
            {"obligation_fulfilled": {"min_burden": 0}},
            {"cost_incurred": {"min_severity": 4}},
            {"stance_defended": {"min_challenges": 0, "min_cost_severity": 1}},
        ],
    )
    def test_v2_rejects_invalid_completion_leaf_bounds(self, leaf):
        raw = _minimal_v2_raw()
        raw["completion_requirements"][0] = {
            "id": "bad_bounds",
            "description": "Invalid bounds.",
            **leaf,
        }
        with pytest.raises(PackCompileError):
            compile_source(raw)

    def test_v2_rejects_future_fields_on_conflict_axis(self):
        raw = _minimal_v2_raw()
        raw["conflict_axes"][0]["activation"] = "after scene 2"
        with pytest.raises(PackCompileError, match="activation"):
            compile_source(raw)

    @pytest.mark.parametrize(
        ("field", "label"),
        [
            ("conflict_axes", "conflict_axis"),
            ("relationship_event_tags", "relationship_event_tag"),
            ("relationship_turning_points", "relationship_turning_point"),
            ("obligation_kinds", "obligation_kind"),
        ],
    )
    def test_v2_rejects_duplicate_dramatic_declaration_ids(self, field, label):
        raw = _minimal_v2_raw()
        raw[field].append(dict(raw[field][0]))
        with pytest.raises(PackCompileError, match=f"duplicate {label} id"):
            compile_source(raw)

    def test_v2_rejects_turning_point_unknown_relationship_tag(self):
        raw = _minimal_v2_raw()
        raw["relationship_turning_points"][0]["all_of_event_tags"] = ["missing"]
        with pytest.raises(PackCompileError, match="unknown tag missing"):
            compile_source(raw)

    def test_v2_rejects_conflict_axis_unknown_character(self):
        raw = _minimal_v2_raw()
        raw["conflict_axes"][0]["source_character_ids"] = ["alice", "missing"]
        with pytest.raises(PackCompileError, match="unknown character missing"):
            compile_source(raw)

    def test_v2_validates_opening_state_location(self):
        raw = _minimal_v2_raw()
        raw["opening_state"]["location"] = "nonexistent_location"
        with pytest.raises(PackCompileError, match="nonexistent_location"):
            compile_source(raw)

    def test_v2_validates_opening_state_known_facts_are_fixed(self):
        raw = _minimal_v2_raw()
        raw["opening_state"]["known_facts"] = ["who_took_notebook"]
        raw["facts"]["latent_questions"] = [
            {
                "id": "who_took_notebook",
                "question": "Who took it?",
                "candidates": [
                    {"value": "alice", "weight": 1.0},
                    {"value": "bob", "weight": 1.0},
                ],
                "commit_when": ["explicit_revelation"],
                "evidence_required": 1,
            }
        ]
        with pytest.raises(PackCompileError, match="opening known fact must be fixed"):
            compile_source(raw)

    def test_v2_skips_fallback_check(self):
        """v2.0 must compile even though it has no fallback endings at all."""
        compiled = compile_source(_minimal_v2_raw())
        assert compiled.source.schema_version == "2.0"


# ---------------------------------------------------------------------------
# Task 4: initial_session_state() v2.0 support
# ---------------------------------------------------------------------------

from src.story.state import FactVisibility, initial_session_state


class TestInitialStateV2:
    def test_v2_initial_state_uses_opening_state(self):
        compiled = compile_source(_minimal_v2_raw())
        state = initial_session_state(compiled, "session_v2_01", session_seed=42)

        assert state.world.location_id == "cafe"
        assert state.world.present_character_ids == ("alice",)
        assert state.world.scene_count == 0
        assert state.world.max_scenes == 20
        # starting_pressure from opening_state
        assert state.world.pressure == 0.1
        # known_facts from opening_state are revealed
        assert state.facts["cafe_is_open"].visibility == FactVisibility.REVEALED

    def test_v2_initial_state_uses_starting_pressure(self):
        raw = _minimal_v2_raw()
        raw["opening_state"]["starting_pressure"] = 0.4
        compiled = compile_source(raw)
        state = initial_session_state(compiled, "session_v2_02", session_seed=7)
        assert state.world.pressure == 0.4

    def test_v1_initial_state_unchanged(self):
        from tests.story_factories import minimal_script_pack_dict

        compiled = compile_source(minimal_script_pack_dict())
        state = initial_session_state(compiled, "session_v1_01", session_seed=1)
        assert state.world.location_id == "cafe"
        assert state.world.pressure == 0.1  # default from v1.0


# ---------------------------------------------------------------------------
# Task 5: minimal_pack_v2_dict() factory
# ---------------------------------------------------------------------------


class TestPackV2Factory:
    def test_factory_produces_compilable_v2_pack(self):
        from tests.story_factories import minimal_pack_v2_dict

        raw = minimal_pack_v2_dict()
        compiled = compile_source(raw)
        assert compiled.source.schema_version == "2.0"
        assert len(compiled.completion_requirement_ids) >= 1
        assert compiled.ending_ids == frozenset()

    def test_factory_has_two_completion_requirements(self):
        from tests.story_factories import minimal_pack_v2_dict

        raw = minimal_pack_v2_dict()
        assert len(raw["completion_requirements"]) == 2

    def test_factory_has_story_history(self):
        from tests.story_factories import minimal_pack_v2_dict

        raw = minimal_pack_v2_dict()
        assert raw["story_history"]["summary"]
        assert len(raw["story_history"]["events"]) >= 1

    def test_factory_has_world_setting_with_forbidden_content(self):
        from tests.story_factories import minimal_pack_v2_dict

        raw = minimal_pack_v2_dict()
        assert raw["world_setting"]["forbidden_content"]
        assert raw["world_setting"]["fact_rules"]

    def test_factory_completion_evidence_references_valid_ids(self):
        from tests.story_factories import minimal_pack_v2_dict

        compiled = compile_source(minimal_pack_v2_dict())
        requirement = compiled.source.completion_requirements[0]
        assert requirement.fact_revealed.fact_id in compiled.fact_ids


# ---------------------------------------------------------------------------
# Task 6: fakes module smoke tests
# ---------------------------------------------------------------------------


class TestFakesModule:
    @pytest.mark.asyncio
    async def test_fake_planner_returns_decision_plan(self):
        from tests.fakes import FakePlanner

        planner = FakePlanner()
        plan = await planner.plan_scene(None, None)
        assert plan.terminal == "decision"
        assert len(plan.choices) == 2

    @pytest.mark.asyncio
    async def test_fake_writer_returns_scene_draft(self):
        from tests.fakes import FakeWriter, valid_decision_plan

        writer = FakeWriter()
        plan = valid_decision_plan()
        draft = await writer.write_scene(None, None, plan)
        assert draft.scene_id == plan.scene_id
        assert len(draft.blocks) >= 1

    @pytest.mark.asyncio
    async def test_fake_streaming_generator_yields_blocks_then_complete(self):
        from tests.fakes import FakeStreamingGenerator

        gen = FakeStreamingGenerator()
        results = []
        async for kind, data in gen.generate_scene(None, None):
            results.append((kind, data))
        # Last should be "complete", all before should be "block"
        assert results[-1][0] == "complete"
        assert all(r[0] == "block" for r in results[:-1])


# ---------------------------------------------------------------------------
# Task 7: yokai_after_school v2.0 migration
# ---------------------------------------------------------------------------

from pathlib import Path

from src.story.script_pack.compiler import compile_script_pack


class TestYokaiAfterSchoolV2:
    def test_yokai_after_school_compiles_as_v2(self):
        pack_path = Path(__file__).resolve().parents[1] / "script_packs" / "yokai_after_school"
        compiled = compile_script_pack(pack_path)
        assert compiled.source.schema_version == "2.0"
        assert len(compiled.completion_requirement_ids) >= 2
        assert compiled.ending_ids == frozenset()

    def test_yokai_after_school_has_no_endings_field(self):
        import yaml

        pack_path = (
            Path(__file__).resolve().parents[1] / "script_packs" / "yokai_after_school" / "pack.yaml"
        )
        raw = yaml.safe_load(pack_path.read_text(encoding="utf-8"))
        assert "endings" not in raw
        assert raw["schema_version"] == "2.0"

    def test_yokai_after_school_has_world_setting(self):
        import yaml

        pack_path = (
            Path(__file__).resolve().parents[1] / "script_packs" / "yokai_after_school" / "pack.yaml"
        )
        raw = yaml.safe_load(pack_path.read_text(encoding="utf-8"))
        assert "world_setting" in raw
        assert raw["world_setting"]["premise"]
        assert len(raw["world_setting"]["locations"]) >= 2

    def test_yokai_after_school_has_story_history(self):
        import yaml

        pack_path = (
            Path(__file__).resolve().parents[1] / "script_packs" / "yokai_after_school" / "pack.yaml"
        )
        raw = yaml.safe_load(pack_path.read_text(encoding="utf-8"))
        assert "story_history" in raw
        assert raw["story_history"]["summary"]

    def test_yokai_after_school_has_opening_state(self):
        import yaml

        pack_path = (
            Path(__file__).resolve().parents[1] / "script_packs" / "yokai_after_school" / "pack.yaml"
        )
        raw = yaml.safe_load(pack_path.read_text(encoding="utf-8"))
        assert "opening_state" in raw
        assert raw["opening_state"]["location"] == "classroom_2b"

    def test_yokai_after_school_completion_requirements_have_evidence(self):
        pack_path = Path(__file__).resolve().parents[1] / "script_packs" / "yokai_after_school"
        compiled = compile_script_pack(pack_path)
        req = compiled.source.completion_requirements[0]
        assert req.all or req.any or req.fact_revealed

    def test_yokai_after_school_preserves_all_facts_goals_characters(self):
        pack_path = Path(__file__).resolve().parents[1] / "script_packs" / "yokai_after_school"
        compiled = compile_script_pack(pack_path)
        assert compiled.character_ids == frozenset({"hiyori", "chika", "mio"})
        assert "transfer_day" in compiled.fact_ids
        assert "paper_fox_sender" in compiled.fact_ids
        assert "hiyori_keep_club" in compiled.goal_ids


# ---------------------------------------------------------------------------
# Task 8: E2E integration verification
# ---------------------------------------------------------------------------


class TestV1V2Coexistence:
    def test_both_v1_and_v2_packs_compile(self):
        from tests.story_factories import minimal_pack_v2_dict, minimal_script_pack_dict

        v1 = compile_source(minimal_script_pack_dict())
        v2 = compile_source(minimal_pack_v2_dict())
        assert v1.source.schema_version == "1.0"
        assert v2.source.schema_version == "2.0"

    def test_v1_has_ending_ids_v2_has_completion_requirement_ids(self):
        from tests.story_factories import minimal_pack_v2_dict, minimal_script_pack_dict

        v1 = compile_source(minimal_script_pack_dict())
        v2 = compile_source(minimal_pack_v2_dict())
        assert len(v1.ending_ids) >= 4
        assert v1.completion_requirement_ids == frozenset()
        assert len(v2.completion_requirement_ids) >= 1
        assert v2.ending_ids == frozenset()

    def test_v2_source_has_no_world_attribute(self):
        from tests.story_factories import minimal_pack_v2_dict

        v2 = compile_source(minimal_pack_v2_dict())
        assert not hasattr(v2.source, "world")
        assert hasattr(v2.source, "world_setting")

    def test_v1_source_has_no_completion_requirements(self):
        from tests.story_factories import minimal_script_pack_dict

        v1 = compile_source(minimal_script_pack_dict())
        assert not hasattr(v1.source, "completion_requirements")
        assert hasattr(v1.source, "world")


class TestCrossPlanTypeExports:
    """Verify that all types required by Plans 2-4 are importable."""

    def test_completion_requirement_source_importable(self):
        from src.story.script_pack.models import CompletionRequirementSource

        req = CompletionRequirementSource(
            id="test_req",
            description="test",
            cost_incurred={"min_severity": 1},
        )
        assert req.id == "test_req"

    def test_completion_evidence_source_importable(self):
        from src.story.script_pack.models import CompletionEvidenceSource

        evidence = CompletionEvidenceSource(cost_incurred={"min_severity": 1})
        assert evidence.cost_incurred.min_severity == 1

    def test_world_setting_source_importable(self):
        from src.story.script_pack.models import WorldSettingSource

        ws = WorldSettingSource(premise="test", locations=[{"id": "loc", "name": "L"}])
        assert ws.premise == "test"

    def test_story_history_source_importable(self):
        from src.story.script_pack.models import StoryHistorySource

        hist = StoryHistorySource(summary="test")
        assert hist.events == ()

    def test_history_event_source_importable(self):
        from src.story.script_pack.models import HistoryEventSource

        evt = HistoryEventSource(summary="test")
        assert evt.participants == ()

    def test_opening_state_source_importable(self):
        from src.story.script_pack.models import OpeningStateSource

        os_ = OpeningStateSource(location="cafe")
        assert os_.time_label == "opening"

    def test_compiled_script_pack_has_completion_requirement_ids(self):
        from tests.story_factories import minimal_pack_v2_dict

        compiled = compile_source(minimal_pack_v2_dict())
        assert isinstance(compiled.completion_requirement_ids, frozenset)

    def test_compiled_script_pack_source_is_v2_instance(self):
        from src.story.script_pack.models import ScriptPackSourceV2
        from tests.story_factories import minimal_pack_v2_dict

        compiled = compile_source(minimal_pack_v2_dict())
        assert isinstance(compiled.source, ScriptPackSourceV2)
