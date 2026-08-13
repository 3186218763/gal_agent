from pathlib import Path

from src.story.script_pack import compile_script_pack

PACK_DIR = Path(__file__).resolve().parents[1] / "script_packs" / "cafe_mystery"


def test_cafe_mystery_pack_compiles_without_fixed_plot():
    compiled = compile_script_pack(PACK_DIR)
    dumped = compiled.source.model_dump(mode="json")

    assert compiled.source.identity.id == "cafe_mystery"
    assert len(compiled.source.characters) == 3
    assert compiled.completion_requirement_ids == frozenset(
        {
            "core_truth_understood",
            "trust_built",
            "irreversible_choice",
        }
    )
    assert compiled.source.schema_version == "2.0"
    assert compiled.ending_ids == frozenset()  # v2.0 has no endings
    assert len(compiled.source.facts.latent_questions) >= 2
    assert "plot" not in dumped
    assert "beats" not in dumped
    assert "scenes" not in dumped


def test_cafe_mystery_pack_hash_is_stable():
    assert compile_script_pack(PACK_DIR).pack_hash == compile_script_pack(PACK_DIR).pack_hash
