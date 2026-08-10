from src.agents.director import build_director_prompt
from src.domain.enums import Phase


def test_director_prompt_includes_goals_and_phase():
    text = build_director_prompt(
        premise="p",
        phase=Phase.RISING,
        tension=6,
        goals_summary="ally_alice:0.2",
        memories=["m1"],
        opening_seed="seed",
        steps=0,
    )
    assert "ally_alice" in text
    assert "rising" in text


def test_director_prompt_includes_memories_and_seed():
    text = build_director_prompt(
        premise="premise text",
        phase=Phase.SETUP,
        tension=3,
        goals_summary="g:0",
        memories=["remembered event"],
        opening_seed="opening seed line",
        steps=0,
    )
    assert "premise text" in text
    assert "remembered event" in text
    assert "opening seed line" in text
    assert "setup" in text
