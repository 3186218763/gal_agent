import pytest

from src.story.conditions import (
    ConditionEvaluationError,
    ConditionSyntaxError,
    compile_condition,
)


def test_compiles_paths_and_evaluates_boolean_expression():
    program = compile_condition(
        "goals.alice_find_ally.completed "
        "and relationships.alice.trust >= 70 "
        "and facts.notebook.truth_status == 'committed'"
    )

    assert program.paths == (
        "facts.notebook.truth_status",
        "goals.alice_find_ally.completed",
        "relationships.alice.trust",
    )
    assert program.evaluate(
        {
            "goals": {"alice_find_ally": {"completed": True}},
            "relationships": {"alice": {"trust": 72}},
            "facts": {"notebook": {"truth_status": "committed"}},
        }
    )


def test_supports_not_in_and_lowercase_literals():
    program = compile_condition(
        "not session.ended and world.location in ['cafe', 'street'] and true"
    )
    assert program.evaluate(
        {
            "session": {"ended": False},
            "world": {"location": "cafe"},
        }
    )


@pytest.mark.parametrize(
    "expression",
    [
        "__import__('os').system('id')",
        "facts['secret']",
        "relationships.alice.trust + 5 > 70",
        "[x for x in facts]",
        "(lambda: true)()",
    ],
)
def test_rejects_executable_or_unsupported_syntax(expression):
    with pytest.raises(ConditionSyntaxError):
        compile_condition(expression)


def test_missing_runtime_path_is_an_explicit_error():
    program = compile_condition("relationships.alice.trust >= 70")
    with pytest.raises(ConditionEvaluationError, match="relationships.alice.trust"):
        program.evaluate({"relationships": {}})
