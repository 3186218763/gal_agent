from src.domain.options import ChoiceOption, PredictedConsequences, GoalEffect
from src.rules.option_validator import validate_options, fallback_options


def _opt(text, flags=None, rel=None, goal=None):
    return ChoiceOption(
        text=text,
        predicted_consequences=PredictedConsequences(
            flag_changes=flags or {},
            relationship_deltas=rel or {},
            goal_effects=[GoalEffect(goal_id=goal, delta_progress=0.1)] if goal else [],
        ),
    )


def test_rejects_too_few():
    r = validate_options(
        [_opt("a", flags={"x": True})],
        valid_character_ids={"alice"},
        valid_goal_ids={"g1"},
        recent_choice_tags=[],
    )
    assert r.valid is False


def test_rejects_identical_consequences():
    a = _opt("one", flags={"x": True})
    b = _opt("two", flags={"x": True})
    r = validate_options(
        [a, b],
        valid_character_ids=set(),
        valid_goal_ids=set(),
        recent_choice_tags=[],
    )
    assert r.valid is False
    assert any("假选择" in i or "fingerprint" in i.lower() or "差分" in i for i in r.issues)


def test_accepts_distinct():
    a = _opt("相信她", flags={"trust_alice": True}, rel={"alice": {"trust": 10}})
    b = _opt("保持警惕", flags={"wary": True}, rel={"alice": {"trust": -5}})
    r = validate_options(
        [a, b],
        valid_character_ids={"alice"},
        valid_goal_ids=set(),
        recent_choice_tags=[],
    )
    assert r.valid is True
    assert 2 <= len(r.options) <= 4


def test_fallback_has_consequences():
    fb = fallback_options()
    assert len(fb) >= 2
    assert all(
        o.predicted_consequences.flag_changes
        or o.predicted_consequences.relationship_deltas
        or o.predicted_consequences.goal_effects
        for o in fb
    )
