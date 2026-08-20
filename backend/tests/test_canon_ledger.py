"""Canon Ledger tests: events, reducer, validator conflicts, context slices."""

from __future__ import annotations

import pytest

from src.story.runtime.segment_context import _canon_ledger_views
from src.story.runtime.segment_contracts import (
    EntityAttributeUpdate,
    PromiseMarkUpdate,
    PromiseSettleUpdate,
    SegmentDraft,
)
from src.story.runtime.validator import ProposalRejected, validate_ledger_updates
from src.story.script_pack import compile_source
from src.story.state import (
    EntityAttributeSet,
    EventEnvelope,
    MotifUsed,
    NarrativePromiseMarked,
    NarrativePromiseSettled,
    SessionState,
    initial_session_state,
)
from tests.story_factories import minimal_script_pack_dict


@pytest.fixture
def pack():
    return compile_source(minimal_script_pack_dict())


@pytest.fixture
def state(pack):
    return initial_session_state(pack, "s_ledger", session_seed=7)


def _draft(updates=()) -> SegmentDraft:
    from src.story.runtime.contracts import SceneDraft
    from src.story.state import NarrativeBlock

    return SegmentDraft(
        segment_id="seg_l1",
        scene_drafts=(
            SceneDraft(
                scene_id="scene_l1",
                blocks=(NarrativeBlock(kind="narration", text="A fresh moment."),),
            ),
        ),
        ledger_updates=tuple(updates),
    )


def _apply(state: SessionState, event) -> SessionState:
    from src.story.state import apply_events

    return apply_events(
        state,
        (
            EventEnvelope(
                session_id=state.session_id,
                sequence=state.revision + 1,
                event=event,
            ),
        ),
    )


# ---------------------------------------------------------------------------
# Reducer
# ---------------------------------------------------------------------------


def test_entity_attribute_set_creates_then_updates(state):
    state = _apply(
        state,
        EntityAttributeSet(
            entity_id="notebook",
            entity_name="艾丽丝的笔记本",
            attribute="cover",
            value="黑色硬皮",
            scene_id="scene_1",
        ),
    )
    entity = state.ledger.entities["notebook"]
    assert entity.name == "艾丽丝的笔记本"
    assert entity.attributes["cover"].value == "黑色硬皮"

    state = _apply(
        state,
        EntityAttributeSet(
            entity_id="notebook",
            entity_name="艾丽丝的笔记本",
            attribute="cover",
            value="磨损的黑色硬皮",
            scene_id="scene_3",
        ),
    )
    assert state.ledger.entities["notebook"].attributes["cover"].value == "磨损的黑色硬皮"


def test_narrative_promise_lifecycle(state):
    state = _apply(
        state,
        NarrativePromiseMarked(
            promise_id="fox_sender", statement="纸狐狸的寄件人尚未揭晓", scene_id="scene_1"
        ),
    )
    assert state.ledger.narrative_promises["fox_sender"].status == "open"

    state = _apply(
        state,
        NarrativePromiseSettled(promise_id="fox_sender", outcome="paid", scene_id="scene_6"),
    )
    assert state.ledger.narrative_promises["fox_sender"].status == "paid"

    from src.story.state import StateTransitionError

    with pytest.raises(StateTransitionError):
        _apply(
            state,
            NarrativePromiseSettled(promise_id="fox_sender", outcome="paid", scene_id="scene_7"),
        )


def test_motif_ring_is_capped(state):
    from src.story.state import MOTIF_RING_CAP

    for index in range(MOTIF_RING_CAP + 5):
        state = _apply(
            state,
            MotifUsed(motif_id=f"gesture_{index}", label=f"姿态{index}", scene_id="scene_x"),
        )
    assert len(state.ledger.recent_motifs) == MOTIF_RING_CAP
    assert state.ledger.recent_motifs[-1].motif_id == f"gesture_{MOTIF_RING_CAP + 4}"


# ---------------------------------------------------------------------------
# Validator: continuity conflicts are actionable rejections
# ---------------------------------------------------------------------------


def test_conflicting_attribute_value_is_rejected_with_old_value_quoted(state):
    state = _apply(
        state,
        EntityAttributeSet(
            entity_id="notebook",
            entity_name="艾丽丝的笔记本",
            attribute="cover",
            value="黑色硬皮",
            scene_id="scene_1",
        ),
    )
    draft = _draft(
        (
            EntityAttributeUpdate(
                entity_id="notebook",
                entity_name="艾丽丝的笔记本",
                attribute="cover",
                value="深蓝硬皮",
            ),
        )
    )
    with pytest.raises(ProposalRejected) as excinfo:
        validate_ledger_updates(state, draft)
    message = str(excinfo.value)
    assert "黑色硬皮" in message, "the established value must be quoted"
    assert "深蓝硬皮" in message, "the conflicting value must be quoted"


def test_same_value_rereads_pass(state):
    state = _apply(
        state,
        EntityAttributeSet(
            entity_id="notebook",
            entity_name="艾丽丝的笔记本",
            attribute="cover",
            value="黑色硬皮",
            scene_id="scene_1",
        ),
    )
    validate_ledger_updates(
        state,
        _draft(
            (
                EntityAttributeUpdate(
                    entity_id="notebook",
                    entity_name="艾丽丝的笔记本",
                    attribute="cover",
                    value="黑色硬皮",
                ),
            )
        ),
    )


def test_intra_draft_conflict_is_rejected(state):
    draft = _draft(
        (
            EntityAttributeUpdate(
                entity_id="ribbon", entity_name="丝带", attribute="color", value="红色"
            ),
            EntityAttributeUpdate(
                entity_id="ribbon", entity_name="丝带", attribute="color", value="蓝色"
            ),
        )
    )
    with pytest.raises(ProposalRejected):
        validate_ledger_updates(state, draft)


def test_settling_unknown_or_paid_promise_lists_open_ids(state):
    state = _apply(
        state,
        NarrativePromiseMarked(
            promise_id="fox_sender", statement="纸狐狸的寄件人尚未揭晓", scene_id="scene_1"
        ),
    )
    with pytest.raises(ProposalRejected) as excinfo:
        validate_ledger_updates(
            state, _draft((PromiseSettleUpdate(promise_id="ghost", outcome="paid"),))
        )
    assert "fox_sender" in str(excinfo.value), "open promise ids are listed for navigation"


def test_duplicate_promise_mark_is_rejected(state):
    state = _apply(
        state,
        NarrativePromiseMarked(
            promise_id="fox_sender", statement="纸狐狸的寄件人尚未揭晓", scene_id="scene_1"
        ),
    )
    with pytest.raises(ProposalRejected):
        validate_ledger_updates(
            state,
            _draft((PromiseMarkUpdate(promise_id="fox_sender", statement="再来一次"),)),
        )


# ---------------------------------------------------------------------------
# Context slices
# ---------------------------------------------------------------------------


def test_empty_ledger_omits_context_section(state):
    assert _canon_ledger_views(state) == {}


def test_ledger_views_expose_cards_promises_and_motif_blacklist(state):
    state = _apply(
        state,
        EntityAttributeSet(
            entity_id="notebook",
            entity_name="艾丽丝的笔记本",
            attribute="cover",
            value="黑色硬皮",
            scene_id="scene_1",
        ),
    )
    state = _apply(
        state,
        MotifUsed(motif_id="push_glasses", label="鲍勃推了推眼镜", scene_id="scene_2"),
    )
    views = _canon_ledger_views(state)
    assert views["entity_cards"][0]["attributes"] == {"cover": "黑色硬皮"}
    assert views["motif_blacklist_recently_used_do_not_reperform"][0]["label"] == "鲍勃推了推眼镜"
