"""Beat Map (v2 additive structure): schema, compilation, DramaManager, effects."""

from __future__ import annotations

import pytest

from src.story.runtime.drama_manager import (
    beat_briefs,
    beat_structure,
    plan_next_segment,
)
from src.story.runtime.pacing import compute_pacing_envelope
from src.story.runtime.segment_contracts import SegmentDraft, SegmentPlan
from src.story.runtime.simulator import segment_events
from src.story.runtime.validator import validate_segment_plan
from src.story.script_pack.compiler import PackCompileError, compile_source
from src.story.state import (
    BeatCompleted,
    EventEnvelope,
    SessionState,
    StateTransitionError,
    apply_event,
    initial_session_state,
)
from tests.story_factories import minimal_pack_v2_dict


def _complete_beat(state: SessionState, beat_id: str, sequence: int) -> SessionState:
    return apply_event(
        state,
        EventEnvelope(
            session_id=state.session_id,
            sequence=sequence,
            event=BeatCompleted(beat_id=beat_id, source_event_id="test"),
        ),
    )


def _beat_pack_dict() -> dict:
    pack = minimal_pack_v2_dict()
    pack["protagonist"]["capabilities"] = ["ask", "observe", "challenge"]
    pack["structure"] = {
        "acts": [
            {
                "id": "act1",
                "scene_budget": (2, 4),
                "target_block_range": (8, 25),
                "beats": [
                    {
                        "id": "b_meet",
                        "kind": "scene",
                        "purpose": "Ren meets Alice at the cafe and senses her unease.",
                        "sketch": {
                            "location_id": "cafe",
                            "present_character_ids": ["alice"],
                        },
                        "must_include": ["the missing notebook is mentioned"],
                    },
                    {
                        "id": "b_first_choice",
                        "kind": "decision",
                        "purpose": "Ren decides how to approach the loss.",
                        "responds_to": ["b_meet"],
                        "choices": [
                            {
                                "option_id": "press",
                                "action_id": "challenge",
                                "label": "Press Alice",
                                "intent": "Ask directly about the notebook.",
                                "target_character_id": "alice",
                                "stance_axis": "trust_vs_evidence",
                                "stance_value": "trust",
                                "relationship_deltas": [
                                    {"character_id": "alice", "axis": "trust", "delta": 10}
                                ],
                            },
                            {
                                "option_id": "watch",
                                "action_id": "observe",
                                "label": "Watch quietly",
                                "intent": "Say nothing and observe.",
                                "outcome": "partial",
                            },
                        ],
                        "effects": {
                            "promise_expectations": ["Ren will find the notebook's truth"],
                        },
                    },
                ],
            },
            {
                "id": "act2",
                "beats": [
                    {
                        "id": "b_alley",
                        "kind": "scene",
                        "purpose": "A clue surfaces in the back alley.",
                        "sketch": {
                            "location_id": "back_alley",
                            "present_character_ids": ["bob"],
                        },
                        "effects": {
                            "commit_latent": [
                                {"fact_id": "who_took_notebook", "value": "alice"}
                            ],
                            "reveal_fact_ids": ["who_took_notebook"],
                            "advance_arc_phase": True,
                            "stance_challenges": [
                                {
                                    "stance_axis": "trust_vs_evidence",
                                    "stance_value": "trust",
                                    "challenging_character_id": "bob",
                                }
                            ],
                        },
                    },
                    {
                        "id": "b_alley_choice",
                        "kind": "decision",
                        "purpose": "Ren decides what to do with the clue.",
                        "responds_to": ["b_alley"],
                        "choices": [
                            {
                                "option_id": "share_clue",
                                "action_id": "ask",
                                "label": "Tell Bob",
                                "intent": "Share the finding with Bob.",
                                "target_character_id": "bob",
                            },
                            {
                                "option_id": "keep_clue",
                                "action_id": "observe",
                                "label": "Keep it",
                                "intent": "Keep the clue to yourself.",
                                "outcome": "partial",
                            },
                        ],
                    },
                ],
            },
            {
                "id": "act3",
                "beats": [
                    {
                        "id": "b_finale",
                        "kind": "ending",
                        "purpose": "The truth settles.",
                        "position": {"min_scene": 2, "max_scene": 20},
                        "requires": "facts.who_took_notebook.truth_status == 'committed'",
                    },
                ],
            },
        ]
    }
    pack["ending_seeds"] = [
        {
            "id": "seed_truth",
            "title": "The Whole Truth",
            "tone": "cathartic",
            "frame": "Ren and Alice face what the notebook really was.",
            "requires": "facts.who_took_notebook.truth_status == 'committed'",
            "must_address": ["who_took_notebook"],
            "priority": 50,
        },
        {
            "id": "seed_fallback",
            "title": "Quiet Close",
            "tone": "quiet",
            "frame": "The evening ends without full answers.",
            "fallback": True,
        },
    ]
    return pack


def _compiled():
    return compile_source(_beat_pack_dict())


def _initial_state(pack=None) -> SessionState:
    pack = pack or _compiled()
    return initial_session_state(pack, "session_beat_test", 7)


# ---------------------------------------------------------------------------
# Schema and compilation
# ---------------------------------------------------------------------------


class TestBeatMapCompilation:
    def test_valid_structure_compiles_with_ids(self):
        pack = _compiled()
        assert pack.beat_ids == {
            "b_meet",
            "b_first_choice",
            "b_alley",
            "b_alley_choice",
            "b_finale",
        }
        assert pack.ending_seed_ids == {"seed_truth", "seed_fallback"}
        # beat/seed requires compile into condition programs
        assert "beat.b_finale.requires" in pack.conditions
        assert "seed.seed_truth.requires" in pack.conditions

    def test_duplicate_beat_id_across_acts_rejected(self):
        raw = _beat_pack_dict()
        raw["structure"]["acts"][1]["beats"][0]["id"] = "b_meet"
        with pytest.raises(PackCompileError, match="duplicate beat id"):
            compile_source(raw)

    def test_unknown_successor_rejected(self):
        raw = _beat_pack_dict()
        raw["structure"]["acts"][0]["beats"][1]["successors"] = ["b_missing"]
        with pytest.raises(PackCompileError, match="successor references unknown beat"):
            compile_source(raw)

    def test_unknown_sketch_location_rejected(self):
        raw = _beat_pack_dict()
        raw["structure"]["acts"][0]["beats"][0]["sketch"]["location_id"] = "rooftop"
        with pytest.raises(PackCompileError, match="sketch references unknown location"):
            compile_source(raw)

    def test_position_beyond_max_scenes_rejected(self):
        raw = _beat_pack_dict()
        raw["structure"]["acts"][1]["beats"][1]["position"] = {"min_scene": 2, "max_scene": 99}
        with pytest.raises(PackCompileError, match="exceeds experience.max_scenes"):
            compile_source(raw)

    def test_choices_on_scene_beat_rejected_at_model_level(self):
        raw = _beat_pack_dict()
        raw["structure"]["acts"][0]["beats"][0]["choices"] = [
            {"option_id": "opt_x", "action_id": "ask", "label": "X", "intent": "x"}
        ]
        with pytest.raises(PackCompileError, match="only allowed on decision beats"):
            compile_source(raw)

    def test_ending_seeds_require_fallback(self):
        raw = _beat_pack_dict()
        raw["ending_seeds"] = [raw["ending_seeds"][0]]
        with pytest.raises(PackCompileError, match="fallback seed"):
            compile_source(raw)

    def test_seeds_without_ending_beat_rejected(self):
        raw = _beat_pack_dict()
        raw["structure"]["acts"][2]["beats"][0]["kind"] = "scene"
        with pytest.raises(PackCompileError, match="require at least one ending beat"):
            compile_source(raw)

    def test_forward_responds_to_rejected(self):
        raw = _beat_pack_dict()
        # a same-act later beat can never complete first
        raw["structure"]["acts"][0]["beats"][0]["responds_to"] = ["b_first_choice"]
        with pytest.raises(PackCompileError, match="does not precede it"):
            compile_source(raw)

    def test_self_responds_to_rejected(self):
        raw = _beat_pack_dict()
        raw["structure"]["acts"][0]["beats"][1]["responds_to"] = [
            "b_first_choice",
            "b_meet",
        ]
        with pytest.raises(PackCompileError, match="does not precede it"):
            compile_source(raw)

    def test_commit_latent_value_outside_candidates_rejected(self):
        raw = _beat_pack_dict()
        raw["structure"]["acts"][1]["beats"][0]["effects"]["commit_latent"] = [
            {"fact_id": "who_took_notebook", "value": "the_postman"}
        ]
        with pytest.raises(PackCompileError, match="outside"):
            compile_source(raw)

    def test_commit_latent_on_fixed_fact_rejected(self):
        raw = _beat_pack_dict()
        raw["structure"]["acts"][1]["beats"][0]["effects"]["commit_latent"] = [
            {"fact_id": "cafe_is_open", "value": "true"}
        ]
        with pytest.raises(PackCompileError, match="commits non-latent fact"):
            compile_source(raw)

    def test_stance_challenge_unknown_axis_rejected(self):
        raw = _beat_pack_dict()
        raw["structure"]["acts"][1]["beats"][0]["effects"]["stance_challenges"] = [
            {"stance_axis": "chaos_vs_order", "stance_value": "chaos"}
        ]
        with pytest.raises(PackCompileError, match="unknown conflict axis"):
            compile_source(raw)

    def test_choice_unknown_action_rejected(self):
        raw = _beat_pack_dict()
        raw["structure"]["acts"][0]["beats"][1]["choices"][0]["action_id"] = "seduce"
        with pytest.raises(PackCompileError, match="unknown action"):
            compile_source(raw)

    def test_must_address_unknown_id_rejected(self):
        raw = _beat_pack_dict()
        raw["ending_seeds"][0]["must_address"] = ["nonexistent_fact"]
        with pytest.raises(PackCompileError, match="must_address references unknown"):
            compile_source(raw)

    def test_pack_without_structure_still_compiles(self):
        pack = compile_source(minimal_pack_v2_dict())
        assert pack.beat_ids == frozenset()
        assert beat_structure(pack) is None


# ---------------------------------------------------------------------------
# DramaManager navigation
# ---------------------------------------------------------------------------


class TestDramaManagerNavigation:
    def test_first_segment_is_scene_then_decision_beat(self):
        pack = _compiled()
        state = _initial_state(pack)
        pacing = compute_pacing_envelope(state, pack)
        plan = plan_next_segment(pack, state, pacing)
        assert plan is not None
        assert plan.terminal == "decision"
        assert plan.beat_ids == ("b_meet", "b_first_choice")
        assert [s.terminal for s in plan.scenes] == ["continue", "decision"]
        # authored choices ride the plan verbatim
        decision = plan.scenes[-1]
        assert [c.option_id for c in decision.choices] == ["press", "watch"]
        assert decision.choices[0].stance_axis == "trust_vs_evidence"

    def test_completed_beats_are_never_reperformed(self):
        pack = _compiled()
        state = _initial_state(pack)
        state = _complete_beat(state, "b_meet", 1)
        pacing = compute_pacing_envelope(state, pack)
        plan = plan_next_segment(pack, state, pacing)
        assert plan is not None
        assert "b_meet" not in plan.beat_ids
        assert plan.beat_ids == ("b_first_choice",)

    def test_forward_cross_act_responds_to_rejected_at_compile(self):
        # A decision beat can only answer a beat that already exists in
        # history; forward cross-act references are a compile error now,
        # not a silent runtime gate.
        raw = _beat_pack_dict()
        raw["structure"]["acts"][0]["beats"][1]["responds_to"] = ["b_alley"]
        with pytest.raises(PackCompileError, match="does not precede it"):
            compile_source(raw)

    def test_ending_beat_requires_commit_condition(self):
        pack = _compiled()
        state = _initial_state(pack)
        state = _complete_beat(state, "b_meet", 1)
        state = _complete_beat(state, "b_first_choice", 2)
        # who_took_notebook is still possible: b_finale requires committed.
        pacing = compute_pacing_envelope(state, pack)
        plan = plan_next_segment(pack, state, pacing)
        assert plan is None or "b_finale" not in plan.beat_ids

    def test_navigation_is_deterministic(self):
        pack = _compiled()
        state = _initial_state(pack)
        pacing = compute_pacing_envelope(state, pack)
        first = plan_next_segment(pack, state, pacing)
        second = plan_next_segment(pack, state, pacing)
        assert first == second

    def test_optional_gated_beat_does_not_block_later_acts(self):
        from src.story.state import FactCommitted

        raw = _beat_pack_dict()
        alley = raw["structure"]["acts"][1]["beats"][0]
        alley["optional"] = True
        # never true on this path: the beat stays gated forever
        alley["requires"] = "session.scene_count >= 99"
        pack = compile_source(raw)
        state = _initial_state(pack)
        state = _complete_beat(state, "b_meet", 1)
        state = _complete_beat(state, "b_first_choice", 2)
        state = _complete_beat(state, "b_alley_choice", 3)
        state = apply_event(
            state,
            EventEnvelope(
                session_id=state.session_id,
                sequence=4,
                event=FactCommitted(
                    fact_id="who_took_notebook",
                    value="alice",
                    evidence_event_ids=("t",),
                ),
            ),
        )
        # reach the pacing convergence window so the ending beat may plan
        # (faked via state, not event replay: the pending-scene handshake
        # makes filler SceneCommitted batches cumbersome here)
        state = state.model_copy(
            update={"world": state.world.model_copy(update={"scene_count": 8})}
        )
        pacing = compute_pacing_envelope(state, pack)
        plan = plan_next_segment(pack, state, pacing)
        # act 2's only incomplete beat is optional → act 3's ending is
        # reachable instead of deadlocking behind the gated scene
        assert plan is not None
        assert plan.terminal == "ending"
        assert plan.beat_ids == ("b_finale",)

    def test_beat_briefs_order_aligned_with_scenes(self):
        pack = _compiled()
        state = _initial_state(pack)
        pacing = compute_pacing_envelope(state, pack)
        plan = plan_next_segment(pack, state, pacing)
        briefs = beat_briefs(pack, plan)
        assert [b["beat_id"] for b in briefs] == list(plan.beat_ids)
        assert briefs[0]["must_include"] == ["the missing notebook is mentioned"]


# ---------------------------------------------------------------------------
# Simulator effects and validator
# ---------------------------------------------------------------------------


def _draft_for(plan: SegmentPlan) -> SegmentDraft:
    from src.story.runtime.contracts import SceneDraft, WrittenChoice
    from src.story.state import NarrativeBlock

    scene_drafts = tuple(
        SceneDraft(
            scene_id=scene.scene_id,
            blocks=(
                NarrativeBlock(kind="narration", text=f"Turn scene {scene.scene_id}"),
            ),
        )
        for scene in plan.scenes
    )
    choices = tuple(
        WrittenChoice(option_id=choice.option_id, label=f"Label {choice.option_id}")
        for scene in plan.scenes
        for choice in scene.choices
    )
    return SegmentDraft(
        segment_id=plan.segment_id,
        scene_drafts=scene_drafts,
        choices=choices,
    )


class TestBeatEffectsSimulation:
    def test_plan_validates_against_kernel(self):
        pack = _compiled()
        state = _initial_state(pack)
        pacing = compute_pacing_envelope(state, pack)
        plan = plan_next_segment(pack, state, pacing)
        assert plan is not None
        validated = validate_segment_plan(pack, state, plan, pacing)
        assert validated.beat_ids == plan.beat_ids

    def test_beat_completed_and_promise_wired(self):
        pack = _compiled()
        state = _initial_state(pack)
        pacing = compute_pacing_envelope(state, pack)
        plan = plan_next_segment(pack, state, pacing)
        events = segment_events(pack, state, plan, _draft_for(plan))
        types = [e.type for e in events]
        assert "beat_completed" in types
        assert "promise_opened" in types
        completed = [e for e in events if e.type == "beat_completed"]
        assert [e.beat_id for e in completed] == ["b_meet", "b_first_choice"]

    def test_reducer_rejects_double_beat_completion(self):
        pack = _compiled()
        state = _initial_state(pack)
        state = _complete_beat(state, "b_meet", 1)
        with pytest.raises(StateTransitionError, match="already completed"):
            apply_event(
                state,
                EventEnvelope(
                    session_id=state.session_id,
                    sequence=2,
                    event=BeatCompleted(beat_id="b_meet", source_event_id="y"),
                ),
            )

    def test_latent_commit_then_reveal_and_arc_advance(self):
        pack = _compiled()
        state = _initial_state(pack)
        # perform act 1 beats
        for i, beat_id in enumerate(("b_meet", "b_first_choice"), start=1):
            state = _complete_beat(state, beat_id, i)
        pacing = compute_pacing_envelope(state, pack)
        plan = plan_next_segment(pack, state, pacing)
        assert plan is not None
        assert plan.beat_ids == ("b_alley", "b_alley_choice")
        events = segment_events(pack, state, plan, _draft_for(plan))
        types = [e.type for e in events]
        # the alley beat commits the latent answer with evidence, then the
        # reveal passes the 1-evidence ladder
        assert "fact_committed" in types
        assert "fact_revealed" in types
        assert "arc_pressure_advanced" in types

    def test_stance_challenge_only_fires_for_expressed_stance(self):
        pack = _compiled()
        state = _initial_state(pack)
        pacing = compute_pacing_envelope(state, pack)
        plan = plan_next_segment(pack, state, pacing)
        events = segment_events(pack, state, plan, _draft_for(plan))
        # no stance expressed yet → b_alley's challenge not in this segment
        # (it is not performed here anyway); ensure no crash and no bogus event
        assert all(e.type != "stance_challenged" for e in events)

    def test_validator_rejects_unknown_and_completed_beats(self):
        from src.story.runtime.validator import ProposalRejected

        pack = _compiled()
        state = _initial_state(pack)
        pacing = compute_pacing_envelope(state, pack)
        plan = plan_next_segment(pack, state, pacing)
        assert plan is not None
        bad = plan.model_copy(update={"beat_ids": ("b_missing",)})
        with pytest.raises(ProposalRejected, match="unknown beat"):
            validate_segment_plan(pack, state, bad, pacing)
        done = plan.model_copy(update={"beat_ids": ("b_meet", "b_first_choice")})
        state = _complete_beat(state, "b_meet", 1)
        with pytest.raises(ProposalRejected, match="already completed"):
            validate_segment_plan(pack, state, done, pacing)


class TestBeatDrivenOrchestrator:
    def test_beat_pack_flows_through_orchestrator(self, tmp_path):
        from src.story.runtime.completion_judge import CompletionJudge
        from src.story.runtime.turn_orchestrator import TurnOrchestrator
        from src.story.storage import StoryEventStore
        from tests.fakes import FakeDirector, FakeGuard, FakePlanner, FakeSegmentWriter
        from tests.test_turn_orchestrator import _collect_events

        pack = _compiled()
        store = StoryEventStore(tmp_path / "beats.db")
        store.create_session(initial_session_state(pack, "s1", session_seed=42))
        orch = TurnOrchestrator(
            store=store,
            director=FakeDirector(),
            writer=FakeSegmentWriter(),
            guard=FakeGuard(),
            completion_judge=CompletionJudge(),
            planner=FakePlanner(),
        )

        opening = _collect_events(orch.execute_turn(pack, "s1", 0, "cmd-open", None))
        ready = next(data for t, data in opening if t == "segment_ready")
        # the DramaManager's authored options, not the FakeDirector defaults
        assert [c["id"] for c in ready["choices"]] == ["press", "watch"]
        events = [env.event for env in store.load_events("s1")]
        assert [e.beat_id for e in events if e.type == "beat_completed"] == [
            "b_meet",
            "b_first_choice",
        ]

        choice_id = ready["choices"][0]["id"]
        second = _collect_events(
            orch.execute_turn(pack, "s1", ready["revision"], f"cmd-c-{choice_id}", choice_id)
        )
        ready2 = next(data for t, data in second if t == "segment_ready")
        assert [c["id"] for c in ready2["choices"]] == ["share_clue", "keep_clue"]
        events = [env.event for env in store.load_events("s1")]
        assert [e.beat_id for e in events if e.type == "beat_completed"] == [
            "b_meet",
            "b_first_choice",
            "b_alley",
            "b_alley_choice",
        ]
        # the alley beat's declarative effects fired end to end
        assert any(e.type == "fact_committed" for e in events)
        assert any(e.type == "fact_revealed" for e in events)
