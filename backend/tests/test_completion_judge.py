from src.story.runtime.completion_judge import CompletionJudge
from src.story.runtime.segment_contracts import CompletionResult
from src.story.script_pack.models import CompletionEvidenceSource, CompletionRequirementSource
from src.story.state import (
    ActionResolved,
    EventEnvelope,
    FactCommitted,
    FactRecord,
    FactRevealed,
    FactTruthStatus,
    FactVisibility,
    SessionState,
    WorldSnapshot,
)


def _make_state(facts=None):
    world = WorldSnapshot(
        location_id="cafe",
        time_label="opening",
        present_character_ids=("alice",),
        max_scenes=20,
        reserved_resolution_scenes=3,
    )
    return SessionState(
        session_id="s1",
        pack_id="test_pack",
        pack_hash="abcd" * 16,
        revision=20,
        session_seed=1,
        world=world,
        facts=facts or {},
        characters={},
    )


def _revealed_fact(fact_id: str) -> FactRecord:
    return FactRecord(
        id=fact_id,
        truth_status=FactTruthStatus.COMMITTED,
        value="alice",
        visibility=FactVisibility.REVEALED,
        evidence_required=1,
        evidence_event_ids=(f"{fact_id}-evidence",),
        committed_by_event_id=f"{fact_id}-committed",
    )


def _fact_trace(fact_id: str, start_sequence: int) -> tuple[EventEnvelope, ...]:
    evidence_id = f"{fact_id}-evidence"
    committed_id = f"{fact_id}-committed"
    revealed_id = f"{fact_id}-revealed"
    return (
        EventEnvelope(
            event_id=evidence_id,
            session_id="s1",
            sequence=start_sequence,
            event=ActionResolved(action_id="observe", outcome="success"),
        ),
        EventEnvelope(
            event_id=committed_id,
            session_id="s1",
            sequence=start_sequence + 1,
            event=FactCommitted(
                fact_id=fact_id,
                value="alice",
                evidence_event_ids=(evidence_id,),
            ),
        ),
        EventEnvelope(
            event_id=revealed_id,
            session_id="s1",
            sequence=start_sequence + 2,
            event=FactRevealed(fact_id=fact_id),
        ),
    )


def test_judge_accepts_new_recursive_fact_evidence_contract():
    facts = {
        "notebook_holder": _revealed_fact("notebook_holder"),
        "disappearance_cause": _revealed_fact("disappearance_cause"),
    }
    requirement = CompletionRequirementSource(
        id="truth_understood",
        description="Understand holder and cause.",
        all=(
            CompletionEvidenceSource(
                fact_revealed={"fact_id": "notebook_holder"},
            ),
            CompletionEvidenceSource(
                any=(
                    CompletionEvidenceSource(
                        fact_revealed={"fact_id": "disappearance_cause"},
                    ),
                    CompletionEvidenceSource(cost_incurred={"min_severity": 1}),
                )
            ),
        ),
    )
    trace = _fact_trace("notebook_holder", 1) + _fact_trace("disappearance_cause", 4)

    result = CompletionJudge().evaluate((requirement,), _make_state(facts), trace)

    assert result.cleared is True
    assert result.assessments[0].satisfied is True
    assert result.assessments[0].cited_event_ids == tuple(envelope.event_id for envelope in trace)


def test_fact_revealed_requires_reveal_event_even_when_final_state_is_revealed():
    facts = {"notebook_holder": _revealed_fact("notebook_holder")}
    requirement = CompletionRequirementSource(
        id="truth_understood",
        description="Understand holder.",
        fact_revealed={"fact_id": "notebook_holder"},
    )
    trace = _fact_trace("notebook_holder", 1)[:-1]

    result = CompletionJudge().evaluate((requirement,), _make_state(facts), trace)

    assert result.cleared is False
    assert "was not revealed by event" in result.assessments[0].rationale


def test_unavailable_semantic_leaf_is_unsatisfied_instead_of_crashing():
    requirement = CompletionRequirementSource(
        id="accepted_cost",
        description="Carry a cost.",
        cost_incurred={"min_severity": 1},
    )

    result = CompletionJudge().evaluate((requirement,), _make_state(), ())

    assert result.cleared is False
    assert result.assessments[0].satisfied is False
    assert "no qualifying cost" in result.assessments[0].rationale


def test_judge_empty_requirements_not_cleared():
    result = CompletionJudge().evaluate((), _make_state(), ())

    assert isinstance(result, CompletionResult)
    assert result.cleared is False
    assert result.assessments == ()
