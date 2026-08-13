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
            "truth_understood",
            "meaningful_bond",
            "accepted_cost",
        }
    )
    assert compiled.source.schema_version == "2.0"
    assert compiled.ending_ids == frozenset()  # v2.0 has no endings
    assert len(compiled.source.facts.latent_questions) >= 2
    assert "plot" not in dumped
    assert "beats" not in dumped
    assert "scenes" not in dumped


def test_cafe_mystery_has_complete_machine_verifiable_dramatic_contract():
    source = compile_script_pack(PACK_DIR).source

    assert source.identity.expected_minutes == 45
    assert source.experience.min_scenes == 8
    assert source.experience.max_scenes == 14
    assert {axis.id for axis in source.conflict_axes} >= {
        "trust_vs_evidence",
        "protection_vs_agency",
    }
    assert {point.id for point in source.relationship_turning_points} == {
        "alice_mutual_trust",
        "bob_earned_respect",
        "mina_shared_responsibility",
    }
    assert {kind.id for kind in source.obligation_kinds} >= {
        "keep_secret",
        "explain_lie",
        "share_risk",
    }
    assert "notebook_disappearance_cause" in {fact.id for fact in source.facts.latent_questions}


def test_cafe_mystery_pack_hash_is_stable():
    assert compile_script_pack(PACK_DIR).pack_hash == compile_script_pack(PACK_DIR).pack_hash
