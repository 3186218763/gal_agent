"""Tests for opening prompt variant and unified segment agent configuration."""

from __future__ import annotations

from src.story.runtime.unified_segment import (
    OPENING_INSTRUCTIONS,
    UNIFIED_INSTRUCTIONS,
)


def test_opening_instructions_exist_and_differ():
    assert OPENING_INSTRUCTIONS
    assert OPENING_INSTRUCTIONS != UNIFIED_INSTRUCTIONS


def test_opening_instructions_mention_long_scene_target():
    """Opening instructions must guide toward long, multi-scene opening."""
    lowered = OPENING_INSTRUCTIONS.lower()
    assert "opening" in lowered
    # Should mention either a scene count target or block count target
    assert "3-5" in OPENING_INSTRUCTIONS or "10-20" in OPENING_INSTRUCTIONS


def test_unified_instructions_have_length_guidance():
    """Normal segment instructions should guide toward longer segments."""
    lowered = UNIFIED_INSTRUCTIONS.lower()
    # Check for some mention of segment length / not rushing
    assert "rush" in lowered or "long" in lowered or "linger" in lowered


def test_opening_instructions_have_planning_and_writing_rules():
    """Opening instructions must still contain planning + writing rule sections."""
    assert "PLANNING" in OPENING_INSTRUCTIONS
    assert "WRITING" in OPENING_INSTRUCTIONS
