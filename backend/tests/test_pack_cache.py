"""Tests for PackCache models and file I/O."""

from __future__ import annotations

from pathlib import Path

import pytest

from src.story.runtime.contracts import (
    ChoicePlan,
    SceneDraft,
    ScenePlan,
    WrittenChoice,
)
from src.story.runtime.pack_cache import CachedOpening, CachedPregen, PackCache
from src.story.runtime.segment_contracts import (
    PacingEnvelope,
    SegmentDraft,
    SegmentPlan,
)
from src.story.state import NarrativeBlock, PresentedChoice, StoryPhase
from src.story.state.events import DecisionPresented, PhaseAdvanced, SceneCommitted

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _pacing() -> PacingEnvelope:
    return PacingEnvelope(
        phase=StoryPhase.OPENING,
        scene_count=0,
        min_scenes=4,
        max_scenes=12,
        reserved_resolution_scenes=2,
        remaining_budget=12,
        can_end=False,
        must_end=False,
        in_convergence=False,
        max_new_threads=3,
        quiet_scene_allowance=2,
        target_block_range=(30, 60),
    )


def _segment_plan(segment_id: str = "seg_opening") -> SegmentPlan:
    return SegmentPlan(
        segment_id=segment_id,
        scenes=(
            ScenePlan(
                scene_id=f"{segment_id}_s1",
                summary="Opening scene.",
                location_id="cafe",
                present_character_ids=("alice",),
                terminal="continue",
            ),
            ScenePlan(
                scene_id=f"{segment_id}_s2",
                summary="Decision scene.",
                location_id="cafe",
                present_character_ids=("alice",),
                terminal="decision",
                decision_id="dec_opening",
                choices=(
                    ChoicePlan(option_id="ask", action_id="ask", intent="ask directly"),
                    ChoicePlan(option_id="observe", action_id="observe", intent="watch carefully"),
                ),
            ),
        ),
        terminal="decision",
    )


def _segment_draft(segment_id: str = "seg_opening") -> SegmentDraft:
    return SegmentDraft(
        segment_id=segment_id,
        scene_drafts=(
            SceneDraft(
                scene_id=f"{segment_id}_s1",
                blocks=(
                    NarrativeBlock(kind="narration", text="The cafe hums quietly."),
                    NarrativeBlock(kind="dialogue", character_id="alice", text="Welcome."),
                ),
            ),
            SceneDraft(
                scene_id=f"{segment_id}_s2",
                blocks=(NarrativeBlock(kind="narration", text="A decision looms."),),
                choices=(
                    WrittenChoice(option_id="ask", label="Ask about the notebook"),
                    WrittenChoice(option_id="observe", label="Watch quietly"),
                ),
            ),
        ),
        choices=(
            WrittenChoice(option_id="ask", label="Ask about the notebook"),
            WrittenChoice(option_id="observe", label="Watch quietly"),
        ),
    )


def _seg_events() -> tuple:
    return (
        PhaseAdvanced(phase=StoryPhase.EXPLORATION),
        SceneCommitted(
            scene_id="seg_opening_s1",
            location_id="cafe",
            present_character_ids=("alice",),
            blocks=(
                NarrativeBlock(kind="narration", text="The cafe hums quietly."),
                NarrativeBlock(kind="dialogue", character_id="alice", text="Welcome."),
            ),
        ),
        SceneCommitted(
            scene_id="seg_opening_s2",
            terminal="decision",
            location_id="cafe",
            present_character_ids=("alice",),
            blocks=(NarrativeBlock(kind="narration", text="A decision looms."),),
            decision_id="dec_opening",
        ),
        DecisionPresented(
            decision_id="dec_opening",
            choices=(
                PresentedChoice(
                    id="ask",
                    action_id="ask",
                    label="Ask about the notebook",
                    intent="ask directly",
                ),
                PresentedChoice(
                    id="observe",
                    action_id="observe",
                    label="Watch quietly",
                    intent="watch carefully",
                ),
            ),
        ),
    )


# ---------------------------------------------------------------------------
# CachedOpening model tests
# ---------------------------------------------------------------------------


class TestCachedOpening:
    def test_json_round_trip(self):
        opening = CachedOpening(
            segment_plan=_segment_plan(),
            segment_draft=_segment_draft(),
            seg_events=_seg_events(),
            pacing=_pacing(),
        )
        json_str = opening.model_dump_json()
        restored = CachedOpening.model_validate_json(json_str)
        assert restored.segment_plan.segment_id == "seg_opening"
        assert len(restored.seg_events) == 4
        assert restored.pacing.target_block_range == (30, 60)

    def test_rejects_extra_fields(self):
        import pydantic

        with pytest.raises(pydantic.ValidationError):
            CachedOpening.model_validate(
                {
                    "segment_plan": _segment_plan().model_dump(mode="json"),
                    "segment_draft": _segment_draft().model_dump(mode="json"),
                    "seg_events": [e.model_dump(mode="json") for e in _seg_events()],
                    "pacing": _pacing().model_dump(mode="json"),
                    "extra_field": "bad",
                }
            )

    def test_seg_events_preserve_discriminator_type(self):
        opening = CachedOpening(
            segment_plan=_segment_plan(),
            segment_draft=_segment_draft(),
            seg_events=_seg_events(),
            pacing=_pacing(),
        )
        restored = CachedOpening.model_validate_json(opening.model_dump_json())
        assert restored.seg_events[0].type == "phase_advanced"
        assert restored.seg_events[1].type == "scene_committed"
        assert restored.seg_events[3].type == "decision_presented"


# ---------------------------------------------------------------------------
# CachedPregen model tests
# ---------------------------------------------------------------------------


class TestCachedPregen:
    def test_json_round_trip(self):
        from src.story.state.events import (
            ActionResolved,
            PlayerActionSelected,
        )

        pre_events = (
            PlayerActionSelected(
                decision_id="dec_opening",
                option_id="ask",
                idempotency_key="test-key",
            ),
            ActionResolved(action_id="ask", outcome="success"),
        )

        pregen = CachedPregen(
            choice_id="ask",
            pre_events=pre_events,
            seg_events=_seg_events(),
            segment_plan=_segment_plan("seg_after_ask"),
            segment_draft=_segment_draft("seg_after_ask"),
            pacing=_pacing(),
        )
        restored = CachedPregen.model_validate_json(pregen.model_dump_json())
        assert restored.choice_id == "ask"
        assert len(restored.pre_events) == 2
        assert restored.pre_events[0].type == "player_action_selected"
        assert restored.segment_plan.segment_id == "seg_after_ask"

    def test_requires_choice_id(self):
        import pydantic

        with pytest.raises(pydantic.ValidationError):
            CachedPregen(
                pre_events=(),
                seg_events=(),
                segment_plan=_segment_plan(),
                segment_draft=_segment_draft(),
                pacing=_pacing(),
            )


# ---------------------------------------------------------------------------
# PackCache I/O tests
# ---------------------------------------------------------------------------


class TestPackCache:
    def test_save_and_load_opening(self, tmp_path: Path):
        cache = PackCache(tmp_path / "cache")
        opening = CachedOpening(
            segment_plan=_segment_plan(),
            segment_draft=_segment_draft(),
            seg_events=_seg_events(),
            pacing=_pacing(),
        )
        pack_hash = "a" * 64
        cache.save_opening(pack_hash, opening)

        assert cache.has_opening(pack_hash)

        loaded = cache.load_opening(pack_hash)
        assert loaded is not None
        assert loaded.segment_plan.segment_id == "seg_opening"

    def test_load_opening_returns_none_when_missing(self, tmp_path: Path):
        cache = PackCache(tmp_path / "cache")
        assert cache.load_opening("b" * 64) is None
        assert cache.has_opening("b" * 64) is False

    def test_save_and_load_pregen(self, tmp_path: Path):
        cache = PackCache(tmp_path / "cache")
        pregen = CachedPregen(
            choice_id="ask",
            pre_events=(),
            seg_events=_seg_events(),
            segment_plan=_segment_plan(),
            segment_draft=_segment_draft(),
            pacing=_pacing(),
        )
        pack_hash = "c" * 64
        cache.save_pregen(pack_hash, "ask", pregen)

        loaded = cache.load_pregen(pack_hash, "ask")
        assert loaded is not None
        assert loaded.choice_id == "ask"

    def test_load_pregen_returns_none_when_missing(self, tmp_path: Path):
        cache = PackCache(tmp_path / "cache")
        assert cache.load_pregen("d" * 64, "ask") is None

    def test_different_pack_hashes_map_to_different_dirs(self, tmp_path: Path):
        cache = PackCache(tmp_path / "cache")
        opening = CachedOpening(
            segment_plan=_segment_plan(),
            segment_draft=_segment_draft(),
            seg_events=_seg_events(),
            pacing=_pacing(),
        )
        cache.save_opening("e" * 64, opening)
        cache.save_opening("f" * 64, opening)

        assert (tmp_path / "cache" / ("e" * 64) / "opening.json").exists()
        assert (tmp_path / "cache" / ("f" * 64) / "opening.json").exists()

    def test_save_opening_creates_nested_dirs(self, tmp_path: Path):
        cache = PackCache(tmp_path / "deep" / "nested" / "cache")
        opening = CachedOpening(
            segment_plan=_segment_plan(),
            segment_draft=_segment_draft(),
            seg_events=_seg_events(),
            pacing=_pacing(),
        )
        cache.save_opening("a" * 64, opening)
        assert cache.has_opening("a" * 64)

    def test_is_complete_checks_all_choices(self, tmp_path: Path):
        cache = PackCache(tmp_path / "cache")
        pack_hash = "g" * 64

        # Empty cache — not complete
        assert not cache.is_complete(pack_hash, ["ask", "observe"])

        # Save opening
        opening = CachedOpening(
            segment_plan=_segment_plan(),
            segment_draft=_segment_draft(),
            seg_events=_seg_events(),
            pacing=_pacing(),
        )
        cache.save_opening(pack_hash, opening)

        # Opening only — not complete
        assert not cache.is_complete(pack_hash, ["ask", "observe"])

        # Save one pregen
        pregen = CachedPregen(
            choice_id="ask",
            pre_events=(),
            seg_events=(),
            segment_plan=_segment_plan(),
            segment_draft=_segment_draft(),
            pacing=_pacing(),
        )
        cache.save_pregen(pack_hash, "ask", pregen)

        # Still not complete — missing "observe"
        assert not cache.is_complete(pack_hash, ["ask", "observe"])

        # Save second pregen
        cache.save_pregen(
            pack_hash,
            "observe",
            CachedPregen(
                choice_id="observe",
                pre_events=(),
                seg_events=(),
                segment_plan=_segment_plan(),
                segment_draft=_segment_draft(),
                pacing=_pacing(),
            ),
        )

        assert cache.is_complete(pack_hash, ["ask", "observe"])
