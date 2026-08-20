"""Runtime repetition gate tests (P0 wiring of the deterministic detector)."""

from __future__ import annotations

import pytest

from src.story.runtime.repetition import (
    draft_repetition_phrases,
    segment_repetition_errors,
)
from src.story.runtime.segment_contracts import (
    SceneDraft,
    SegmentDraft,
)
from src.story.script_pack import compile_source
from src.story.state import NarrativeBlock, ProseBlockRecord, initial_session_state
from tests.story_factories import minimal_script_pack_dict


@pytest.fixture
def pack():
    return compile_source(minimal_script_pack_dict())


@pytest.fixture
def state(pack):
    return initial_session_state(pack, "session_01", session_seed=42)


def _with_committed(state, *texts):
    records = tuple(
        ProseBlockRecord(scene_id="scene_prev", kind="narration", text=text)
        for text in texts
    )
    return state.model_copy(update={"recent_prose_blocks": records})


def _draft_with_text(*texts: str) -> SegmentDraft:
    return SegmentDraft(
        segment_id="seg_1",
        scene_drafts=(
            SceneDraft(
                scene_id="scene_01",
                blocks=tuple(
                    NarrativeBlock(kind="narration", text=text) for text in texts
                ),
            ),
        ),
    )


def test_no_committed_prose_passes(pack, state):
    assert segment_repetition_errors(pack, state, _draft_with_text("Brand new content.")) == []


def test_long_repeated_phrase_is_rejected_with_quoted_evidence(pack, state):
    state = _with_committed(state, "她推了推眼镜，把便签折成了纸狐狸的形状。")
    errors = segment_repetition_errors(
        pack, state, _draft_with_text("他又推了推眼镜，把便签折成了纸狐狸的形状。")
    )
    assert errors, "a re-run long phrase must be rejected"
    assert any("推了推眼镜" in error for error in errors)


def test_proper_nouns_do_not_trigger(pack, state):
    """Character/location names legitimately recur and are stoplisted."""
    state = _with_committed(state, "Alice stood at the Cafe, watching the rain.")
    assert (
        segment_repetition_errors(
            pack, state, _draft_with_text("Alice returned to the Cafe; the story moved on.")
        )
        == []
    )


def test_distinct_new_prose_passes(pack, state):
    state = _with_committed(state, "Alice waited by the window, stirring her cup slowly.")
    assert (
        segment_repetition_errors(
            pack, state, _draft_with_text("Ren pushed the door open and shook off the rain.")
        )
        == []
    )


def test_draft_repetition_phrases_orders_longest_first(pack, state):
    phrases = draft_repetition_phrases(
        pack,
        ["折纸狐狸安静地躺在便签上面。"],
        _draft_with_text("折纸狐狸安静地躺在便签上面，无人打扰。"),
    )
    assert phrases, "identical text must share phrases"
    assert phrases == sorted(phrases, key=len, reverse=True)
