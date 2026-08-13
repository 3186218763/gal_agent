from dataclasses import dataclass

from src.story.runtime.completion_judge import CompletionJudge
from src.story.runtime.segment_contracts import CompletionResult
from src.story.state import (
    EventEnvelope,
    FactCommitted,
    FactRecord,
    FactTruthStatus,
    FactVisibility,
    GoalAdvanced,
    GoalRuntime,
    GoalStatus,
    SessionState,
    WorldSnapshot,
)


@dataclass(frozen=True)
class _ReqHint:
    fact_ids: tuple = ()
    goal_ids: tuple = ()


@dataclass(frozen=True)
class _Requirement:
    id: str
    description: str
    evidence_hints: _ReqHint = _ReqHint()


def _make_requirement(req_id="req_a", fact_ids=(), goal_ids=()):
    return _Requirement(
        id=req_id,
        description=f"Requirement {req_id}",
        evidence_hints=_ReqHint(fact_ids=fact_ids, goal_ids=goal_ids),
    )


def _make_state(facts=None, goals=None):
    world = WorldSnapshot(
        location_id="cafe",
        time_label="opening",
        present_character_ids=("alice",),
        max_scenes=20,
        reserved_resolution_scenes=3,
        goals=goals or {},
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


def test_judge_satisfied_by_committed_fact():
    facts = {
        "core_cause": FactRecord(
            id="core_cause",
            truth_status=FactTruthStatus.COMMITTED,
            value="alice",
            visibility=FactVisibility.REVEALED,
            evidence_required=1,
            evidence_event_ids=("evt-1",),
        ),
    }
    state = _make_state(facts=facts)
    reqs = (
        _Requirement(
            id="core_truth",
            description="Understand the cause.",
            evidence_hints=_ReqHint(fact_ids=("core_cause",)),
        ),
    )
    trace = (
        EventEnvelope(
            session_id="s1",
            sequence=5,
            event=FactCommitted(fact_id="core_cause", value="alice", evidence_event_ids=("evt-1",)),
        ),
    )
    judge = CompletionJudge()
    result = judge.evaluate(reqs, state, trace)
    assert result.cleared is True
    assert result.assessments[0].satisfied is True
    assert len(result.assessments[0].cited_event_ids) > 0


def test_judge_satisfied_by_fact_and_goal():
    facts = {
        "core_cause": FactRecord(
            id="core_cause",
            truth_status=FactTruthStatus.COMMITTED,
            value="alice",
            visibility=FactVisibility.REVEALED,
            evidence_required=1,
            evidence_event_ids=("evt-1",),
        ),
    }
    goals = {
        "find_ally": GoalRuntime(goal_id="find_ally", status=GoalStatus.COMPLETED, progress=1.0),
    }
    state = _make_state(facts=facts, goals=goals)
    reqs = (
        _make_requirement(req_id="req_a", fact_ids=("core_cause",)),
        _make_requirement(req_id="req_b", goal_ids=("find_ally",)),
    )
    trace = (
        EventEnvelope(
            session_id="s1",
            sequence=5,
            event=FactCommitted(fact_id="core_cause", value="alice", evidence_event_ids=("evt-1",)),
        ),
        EventEnvelope(
            session_id="s1",
            sequence=10,
            event=GoalAdvanced(goal_id="find_ally", delta=0.5),
        ),
    )
    judge = CompletionJudge()
    result = judge.evaluate(reqs, state, trace)
    assert result.cleared is True
    assert result.assessments[0].satisfied is True
    assert result.assessments[1].satisfied is True


def test_judge_not_satisfied_by_uncommitted_fact():
    facts = {
        "core_cause": FactRecord(
            id="core_cause",
            truth_status=FactTruthStatus.POSSIBLE,
            value=None,
            visibility=FactVisibility.HIDDEN,
            evidence_required=1,
        ),
    }
    state = _make_state(facts=facts)
    reqs = (
        _Requirement(
            id="core_truth",
            description="Understand the cause.",
            evidence_hints=_ReqHint(fact_ids=("core_cause",)),
        ),
    )
    trace = ()
    judge = CompletionJudge()
    result = judge.evaluate(reqs, state, trace)
    assert result.cleared is False
    assert result.assessments[0].satisfied is False


def test_judge_satisfied_by_completed_goal():
    goals = {
        "find_ally": GoalRuntime(goal_id="find_ally", status=GoalStatus.COMPLETED, progress=1.0),
    }
    state = _make_state(goals=goals)
    reqs = (
        _Requirement(
            id="ally",
            description="Find an ally.",
            evidence_hints=_ReqHint(goal_ids=("find_ally",)),
        ),
    )
    trace = (
        EventEnvelope(
            session_id="s1",
            sequence=10,
            event=GoalAdvanced(goal_id="find_ally", delta=0.5),
        ),
    )
    judge = CompletionJudge()
    result = judge.evaluate(reqs, state, trace)
    assert result.cleared is True


def test_judge_no_hints_unsatisfied():
    state = _make_state()
    reqs = (_Requirement(id="vague", description="Something."),)
    trace = ()
    judge = CompletionJudge()
    result = judge.evaluate(reqs, state, trace)
    assert result.cleared is False
    assert "no evidence hints" in result.assessments[0].rationale


def test_judge_multiple_requirements_partial():
    facts = {
        "fact_a": FactRecord(
            id="fact_a",
            truth_status=FactTruthStatus.COMMITTED,
            value=True,
            visibility=FactVisibility.REVEALED,
            evidence_required=1,
            evidence_event_ids=("e1",),
        ),
    }
    state = _make_state(facts=facts)
    reqs = (
        _Requirement(
            id="req_a",
            description="A",
            evidence_hints=_ReqHint(fact_ids=("fact_a",)),
        ),
        _Requirement(
            id="req_b",
            description="B",
            evidence_hints=_ReqHint(fact_ids=("fact_missing",)),
        ),
    )
    trace = ()
    judge = CompletionJudge()
    result = judge.evaluate(reqs, state, trace)
    assert result.cleared is False
    assert result.assessments[0].satisfied is True
    assert result.assessments[1].satisfied is False


def test_judge_empty_requirements_not_cleared():
    """An empty requirements tuple should not clear — no requirements to satisfy."""
    state = _make_state()
    judge = CompletionJudge()
    result = judge.evaluate((), state, ())
    assert isinstance(result, CompletionResult)
    assert result.cleared is False
    assert result.assessments == ()


def test_judge_with_real_completion_requirement_source():
    """Judge should accept the real CompletionRequirementSource from script_pack.models."""
    from src.story.script_pack.models import (
        CompletionRequirementSource,
        EvidenceHintsSource,
    )

    facts = {
        "core_cause": FactRecord(
            id="core_cause",
            truth_status=FactTruthStatus.COMMITTED,
            value="alice",
            visibility=FactVisibility.REVEALED,
            evidence_required=1,
            evidence_event_ids=("evt-1",),
        ),
    }
    state = _make_state(facts=facts)
    reqs = (
        CompletionRequirementSource(
            id="req1",
            description="Understand the cause.",
            evidence_hints=EvidenceHintsSource(fact_ids=("core_cause",)),
        ),
    )
    trace = (
        EventEnvelope(
            session_id="s1",
            sequence=5,
            event=FactCommitted(fact_id="core_cause", value="alice", evidence_event_ids=("evt-1",)),
        ),
    )
    judge = CompletionJudge()
    result = judge.evaluate(reqs, state, trace)
    assert result.cleared is True
    assert result.assessments[0].satisfied is True
