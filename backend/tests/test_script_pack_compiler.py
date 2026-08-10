from pathlib import Path

import pytest
import yaml

from src.story.script_pack.compiler import (
    PackCompileError,
    compile_script_pack,
    compile_source,
    load_script_pack_source,
)
from tests.story_factories import minimal_script_pack_dict


def test_compile_source_collects_conditions_and_stable_hash():
    raw = minimal_script_pack_dict()

    first = compile_source(raw)
    second = compile_source(raw)

    assert first.pack_hash == second.pack_hash
    assert len(first.pack_hash) == 64
    assert "ending.ally_ending.all.0" in first.conditions
    assert first.conditions["goal.alice_find_ally.success"].paths == (
        "relationships.alice.trust",
    )
    assert first.character_ids == frozenset({"alice"})
    assert first.action_ids >= {"ask", "observe", "support", "challenge"}


def test_compile_rejects_duplicate_fact_ids():
    raw = minimal_script_pack_dict()
    raw["facts"]["fixed"].append(dict(raw["facts"]["fixed"][0]))

    with pytest.raises(PackCompileError, match="duplicate fact id"):
        compile_source(raw)


def test_compile_rejects_unknown_character_reference():
    raw = minimal_script_pack_dict()
    raw["facts"]["fixed"][0]["known_by"] = ["missing_character"]

    with pytest.raises(PackCompileError, match="missing_character"):
        compile_source(raw)


def test_compile_rejects_unknown_fact_in_condition():
    raw = minimal_script_pack_dict()
    raw["endings"][0]["eligibility"]["all"] = [
        "facts.missing_fact.truth_status == 'committed'"
    ]

    with pytest.raises(PackCompileError, match="missing_fact"):
        compile_source(raw)


def test_compile_rejects_duplicate_location_ids():
    raw = minimal_script_pack_dict()
    raw["world"]["locations"].append(dict(raw["world"]["locations"][0]))

    with pytest.raises(PackCompileError, match="duplicate location id"):
        compile_source(raw)


def test_compile_rejects_nonfixed_opening_fact():
    raw = minimal_script_pack_dict()
    raw["world"]["initial_situation"]["known_facts"] = ["who_took_notebook"]

    with pytest.raises(PackCompileError, match="opening known fact must be fixed"):
        compile_source(raw)


def test_compile_rejects_character_knowledge_of_uncommitted_fact():
    raw = minimal_script_pack_dict()
    raw["characters"][0]["knowledge"] = ["who_took_notebook"]

    with pytest.raises(PackCompileError, match="opening knowledge must be fixed"):
        compile_source(raw)


def test_compile_rejects_unknown_disabled_action():
    raw = minimal_script_pack_dict()
    raw["interaction_rules"]["disabled"] = ["teleport"]

    with pytest.raises(PackCompileError, match="unknown disabled action"):
        compile_source(raw)


def test_compile_requires_fallback_reachable_by_max_scene_count():
    raw = minimal_script_pack_dict()
    raw["endings"][-1]["eligibility"]["all"] = ["session.scene_count >= 99"]

    with pytest.raises(PackCompileError, match="guaranteed fallback"):
        compile_source(raw)


def test_modular_pack_loader_resolves_safe_includes(tmp_path: Path):
    raw = minimal_script_pack_dict()
    characters = raw.pop("characters")
    raw["includes"] = {"characters": "characters.yaml"}
    (tmp_path / "pack.yaml").write_text(
        yaml.safe_dump(raw, allow_unicode=True, sort_keys=False),
        encoding="utf-8",
    )
    (tmp_path / "characters.yaml").write_text(
        yaml.safe_dump(characters, allow_unicode=True, sort_keys=False),
        encoding="utf-8",
    )

    source = load_script_pack_source(tmp_path)

    assert source.characters[0].id == "alice"
    assert compile_script_pack(tmp_path).source.identity.id == "test_pack"


def test_modular_pack_loader_rejects_parent_traversal(tmp_path: Path):
    raw = minimal_script_pack_dict()
    raw.pop("characters")
    raw["includes"] = {"characters": "../characters.yaml"}
    (tmp_path / "pack.yaml").write_text(
        yaml.safe_dump(raw, allow_unicode=True, sort_keys=False),
        encoding="utf-8",
    )

    with pytest.raises(PackCompileError, match="inside the pack directory"):
        load_script_pack_source(tmp_path)
