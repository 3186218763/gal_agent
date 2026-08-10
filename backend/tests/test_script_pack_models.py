import pytest
from pydantic import ValidationError

from src.story.script_pack.models import ScriptPackSource
from tests.story_factories import minimal_script_pack_dict


def test_valid_script_pack_source_is_frozen_and_typed():
    source = ScriptPackSource.model_validate(minimal_script_pack_dict())

    assert source.identity.id == "test_pack"
    assert source.experience.reserved_resolution_scenes == 3
    assert source.characters[0].initial_relationship["trust"] == 35
    assert source.facts.latent_questions[0].evidence_required == 1

    with pytest.raises(ValidationError):
        source.identity.title = "Changed"


def test_unknown_author_field_is_rejected():
    raw = minimal_script_pack_dict()
    raw["identity"]["unexpected"] = "typo"

    with pytest.raises(ValidationError, match="unexpected"):
        ScriptPackSource.model_validate(raw)


def test_invalid_id_is_rejected():
    raw = minimal_script_pack_dict()
    raw["characters"][0]["id"] = "Alice Has Spaces"

    with pytest.raises(ValidationError):
        ScriptPackSource.model_validate(raw)


def test_action_effect_bounds_stay_within_kernel_limits():
    raw = minimal_script_pack_dict()
    raw["interaction_rules"]["extensions"] = [
        {
            "id": "reassure",
            "effects": {
                "relationship_axes": {"trust": [-101, 20]},
            },
        }
    ]

    with pytest.raises(ValidationError, match="must stay within -100..100"):
        ScriptPackSource.model_validate(raw)
