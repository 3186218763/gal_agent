from src.agents.character import build_character_prompt


def test_character_prompt():
    p = build_character_prompt(
        name="艾丽丝",
        personality="冲动",
        trust=55,
        directive="试探玩家",
        memories=["刚坐下"],
    )
    assert "艾丽丝" in p and "冲动" in p


def test_character_prompt_includes_trust_directive_memories():
    p = build_character_prompt(
        name="鲍勃",
        personality="谨慎",
        trust=30,
        directive="警告玩家",
        memories=["听到争吵"],
    )
    assert "鲍勃" in p
    assert "谨慎" in p
    assert "30" in p
    assert "警告玩家" in p
    assert "听到争吵" in p
