"""Scene Performance Loop (P2): per-scene generation, seam anchors, block repair."""

from __future__ import annotations

import pytest

from src.story.runtime.pacing import compute_pacing_envelope
from src.story.runtime.scene_performer import (
    assemble_segment_draft,
    block_targets,
    seam_tail_blocks,
)
from src.story.runtime.segment_contracts import GuardResult, GuardViolation
from src.story.runtime.turn_orchestrator import TurnOrchestrator
from src.story.script_pack.compiler import compile_source
from src.story.state import initial_session_state
from src.story.storage import StoryEventStore
from tests.fakes import (
    FakeDirector,
    FakeGuard,
    FakePlanner,
    FakeScenePerformer,
)
from tests.test_beat_map import _beat_pack_dict
from tests.test_turn_orchestrator import _collect_events


class _FlakyGuard:
    """Passes except on the first N checks, failing on one named block."""

    def __init__(self, failures: int, block_index: int = 0) -> None:
        self.failures = failures
        self.block_index = block_index
        self.checks = 0

    def check_segment(self, pack, state, plan, draft) -> GuardResult:
        self.checks += 1
        if self.checks <= self.failures:
            return GuardResult(
                passed=False,
                violations=(
                    GuardViolation(
                        kind="contradiction",
                        block_index=self.block_index,
                        detail="the notebook cover was already established as black",
                    ),
                ),
            )
        return GuardResult(passed=True)


def _beat_orchestrator(store, guard=None, performer=None):
    return TurnOrchestrator(
        store=store,
        director=FakeDirector(),
        writer=None,
        guard=guard or FakeGuard(),
        completion_judge=_completion_judge(),
        planner=FakePlanner(),
        scene_performer=performer or FakeScenePerformer(),
    )


def _completion_judge():
    from src.story.runtime.completion_judge import CompletionJudge

    return CompletionJudge()


def _beat_pack():
    return compile_source(_beat_pack_dict())


class TestSceneLoopUnits:
    def test_block_targets_maps_global_to_local(self):
        from src.story.runtime.contracts import SceneDraft
        from src.story.state import NarrativeBlock

        def scene(n_blocks: int, scene_id: str) -> SceneDraft:
            return SceneDraft(
                scene_id=scene_id,
                blocks=tuple(
                    NarrativeBlock(kind="narration", text=f"{scene_id}-{i}")
                    for i in range(n_blocks)
                ),
            )

        drafts = (scene(3, "s1"), scene(2, "s2"))
        targets = block_targets(drafts, (1, 2, 4))
        assert targets == {0: (1, 2), 1: (1,)}

    def test_seam_tail_is_previous_scene_verbatim(self):
        from src.story.runtime.contracts import SceneDraft
        from src.story.state import NarrativeBlock

        first = SceneDraft(
            scene_id="s1",
            blocks=tuple(
                NarrativeBlock(kind="narration", text=f"b{i}") for i in range(5)
            ),
        )
        tail = seam_tail_blocks((first,), 1)
        assert [block.text for block in tail] == ["b2", "b3", "b4"]
        assert seam_tail_blocks((first,), 0) == ()

    def test_assemble_collects_choices_in_order(self):
        from src.story.runtime.contracts import SceneDraft, WrittenChoice
        from src.story.runtime.drama_manager import plan_next_segment
        from src.story.state import NarrativeBlock

        pack = _beat_pack()
        state = initial_session_state(pack, "s", 1)
        plan = plan_next_segment(
            pack, state, compute_pacing_envelope(state, pack)
        )
        assert plan is not None
        drafts = tuple(
            SceneDraft(
                scene_id=scene.scene_id,
                blocks=(NarrativeBlock(kind="narration", text="x"),),
                choices=tuple(
                    WrittenChoice(option_id=choice.option_id, label=choice.intent[:80])
                    for choice in scene.choices
                )
                if scene.terminal == "decision"
                else (),
            )
            for scene in plan.scenes
        )
        draft = assemble_segment_draft(plan, drafts)
        assert [c.option_id for c in draft.choices] == ["press", "watch"]


class TestScenePerformanceLoop:
    def test_scenes_performed_one_call_each_with_seam_anchors(self, tmp_path):
        FakeScenePerformer.seams_seen.clear()
        pack = _beat_pack()
        store = StoryEventStore(tmp_path / "perf.db")
        store.create_session(initial_session_state(pack, "s1", session_seed=42))
        orch = _beat_orchestrator(store)

        events = _collect_events(orch.execute_turn(pack, "s1", 0, "cmd-open", None))
        ready = next(data for t, data in events if t == "segment_ready")

        # two scenes performed (b_meet, b_first_choice), authored choices surfaced
        assert [c["id"] for c in ready["choices"]] == ["press", "watch"]
        # scene 2's seam is scene 1's final blocks, verbatim
        assert len(FakeScenePerformer.seams_seen) == 2
        assert FakeScenePerformer.seams_seen[0] == ()
        committed = [
            envelope.event
            for envelope in store.load_events("s1")
            if envelope.event.type == "scene_committed"
        ]
        first_scene_tail = [block.text for block in committed[0].blocks[-3:]]
        seam = FakeScenePerformer.seams_seen[1]
        assert seam, "scene 2 must receive scene 1's tail"
        assert list(seam) == first_scene_tail

    def test_block_repair_targets_failing_block_only(self, tmp_path):
        FakeScenePerformer.repairs.clear()
        pack = _beat_pack()
        store = StoryEventStore(tmp_path / "repair.db")
        store.create_session(initial_session_state(pack, "s1", session_seed=42))
        guard = _FlakyGuard(failures=1, block_index=1)
        orch = _beat_orchestrator(store, guard=guard)

        events = _collect_events(orch.execute_turn(pack, "s1", 0, "cmd-open", None))
        assert any(t == "segment_ready" for t, _ in events)
        # the first check failed on global block 1 → scene 0, local block 1
        assert FakeScenePerformer.repairs == [(0, (1,))]
        assert guard.checks == 2  # rejected once, repaired, passed

    def test_repair_budget_exhaustion_fails_closed(self, tmp_path):
        from src.story.runtime.contracts import RuntimeGenerationUnavailable

        FakeScenePerformer.repairs.clear()
        pack = _beat_pack()
        store = StoryEventStore(tmp_path / "repair_dead.db")
        store.create_session(initial_session_state(pack, "s1", session_seed=42))
        guard = _FlakyGuard(failures=99)
        orch = _beat_orchestrator(store, guard=guard)

        with pytest.raises(RuntimeGenerationUnavailable):
            _collect_events(orch.execute_turn(pack, "s1", 0, "cmd-open", None))
        # exactly the repair budget (2) was spent before fail-closed
        assert len(FakeScenePerformer.repairs) == 2
        # nothing committed
        assert not store.load_events("s1")

    def test_authored_choice_resolves_without_planner(self, tmp_path):
        """Beat packs need no planner: the authored outcome/deltas resolve."""
        pack = _beat_pack()
        store = StoryEventStore(tmp_path / "authored.db")
        store.create_session(initial_session_state(pack, "s1", session_seed=42))
        orch = TurnOrchestrator(
            store=store,
            director=FakeDirector(),
            writer=None,
            guard=FakeGuard(),
            completion_judge=_completion_judge(),
            planner=None,
            scene_performer=FakeScenePerformer(),
        )

        opening = _collect_events(orch.execute_turn(pack, "s1", 0, "cmd-open", None))
        ready = next(data for t, data in opening if t == "segment_ready")
        choice_id = ready["choices"][0]["id"]
        second = _collect_events(
            orch.execute_turn(
                pack, "s1", ready["revision"], f"cmd-c-{choice_id}", choice_id
            )
        )
        # the authored consequence carried into act 2 without any planner
        assert any(t == "segment_ready" for t, _ in second)
        events = [env.event for env in store.load_events("s1")]
        resolved = [e for e in events if e.type == "action_resolved"]
        assert resolved and resolved[0].outcome == "success"
        trust_deltas = [
            e
            for e in events
            if e.type == "relationship_changed" and e.axis == "trust"
        ]
        assert any(e.delta == 10 and e.character_id == "alice" for e in trust_deltas)
