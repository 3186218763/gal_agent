from pathlib import Path

from src.story.script_pack import compile_script_pack

PACK_DIR = Path(__file__).resolve().parents[1] / "script_packs" / "yokai_after_school"


def test_yokai_after_school_pack_compiles_without_fixed_plot():
    compiled = compile_script_pack(PACK_DIR)
    dumped = compiled.source.model_dump(mode="json")

    assert compiled.source.identity.id == "yokai_after_school"
    assert compiled.source.identity.title == "放学后，狐签与心跳"
    assert compiled.source.protagonist.name == "陆言"
    assert len(compiled.source.characters) == 3
    assert compiled.completion_requirement_ids == frozenset(
        {
            "strange_welcome_understood",
            "club_has_direction",
            "meaningful_bond",
            "accepted_cost",
        }
    )
    assert compiled.source.schema_version == "2.0"
    assert compiled.ending_ids == frozenset()
    assert len(compiled.source.facts.latent_questions) >= 3
    assert "plot" not in dumped
    assert "beats" not in dumped
    assert "scenes" not in dumped


def test_yokai_after_school_has_complete_machine_verifiable_dramatic_contract():
    source = compile_script_pack(PACK_DIR).source

    assert source.identity.expected_minutes == 45
    assert source.experience.min_scenes == 8
    assert source.experience.max_scenes == 14
    assert {axis.id for axis in source.conflict_axes} >= {
        "order_vs_spontaneity",
        "privacy_vs_expression",
        "guest_vs_member",
    }
    assert {point.id for point in source.relationship_turning_points} == {
        "hiyori_accepts_help",
        "chika_drops_the_act",
        "mio_shares_the_shrine",
    }
    assert {kind.id for kind in source.obligation_kinds} >= {
        "keep_private_wish",
        "finish_club_task",
        "share_public_risk",
    }
    assert "paper_fox_sender" in {fact.id for fact in source.facts.latent_questions}
    assert "club_future" in {fact.id for fact in source.facts.latent_questions}


def test_yokai_after_school_pack_hash_is_stable():
    assert compile_script_pack(PACK_DIR).pack_hash == compile_script_pack(PACK_DIR).pack_hash


def test_pack_targets_a_30_45_minute_playthrough_with_bounded_convergence():
    source = compile_script_pack(PACK_DIR).source
    experience = source.experience

    assert 30 <= source.identity.expected_minutes <= 45
    assert experience.min_scenes >= 6
    assert experience.max_scenes <= 20
    assert 4 <= experience.max_scenes - experience.min_scenes <= 8
    assert 1 <= experience.reserved_resolution_scenes < experience.min_scenes


def test_open_questions_are_machine_committable():
    source = compile_script_pack(PACK_DIR).source
    questions = {fact.id: fact for fact in source.facts.latent_questions}

    assert len(questions) >= 3
    for question in questions.values():
        assert len(question.candidates) >= 2
        assert question.evidence_required >= 1
        assert set(question.commit_when) >= {
            "first_irreversible_evidence",
            "explicit_revelation",
        }


def test_turning_points_anchor_on_engine_relationship_events():
    source = compile_script_pack(PACK_DIR).source
    relationships = {
        character.id: set(character.initial_relationship) for character in source.characters
    }
    anchors = {tag.id for tag in source.relationship_event_tags}

    assert len(source.relationship_turning_points) >= 3
    for turning_point in source.relationship_turning_points:
        assert turning_point.character_id in relationships
        for tag in turning_point.all_of_event_tags:
            assert tag in anchors
            assert tag.startswith("relationship_changed_")
            axis = tag.removeprefix("relationship_changed_")
            assert axis in relationships[turning_point.character_id]
        assert turning_point.min_distinct_source_choices >= 1


def test_obligation_kinds_are_bounded_and_resolvable():
    source = compile_script_pack(PACK_DIR).source
    assert len(source.obligation_kinds) >= 3
    for kind in source.obligation_kinds:
        assert 1 <= kind.burden <= 3
        assert "fulfilled" in kind.allowed_outcomes


def test_completion_requirements_are_reachable_by_the_engine():
    from src.story.script_pack.compiler import _walk_completion_evidence

    source = compile_script_pack(PACK_DIR).source
    latent_ids = {fact.id for fact in source.facts.latent_questions}
    turning_point_ids = {point.id for point in source.relationship_turning_points}
    burdens = {kind.burden for kind in source.obligation_kinds}
    required = {requirement.id for requirement in source.completion_requirements}

    assert required >= {
        "strange_welcome_understood",
        "club_has_direction",
        "meaningful_bond",
        "accepted_cost",
    }
    leaves = [
        leaf
        for requirement in source.completion_requirements
        for leaf in _walk_completion_evidence(requirement)
    ]
    assert leaves

    for leaf in leaves:
        if leaf.fact_revealed is not None:
            assert leaf.fact_revealed.fact_id in latent_ids
        elif leaf.relationship_turning_point is not None:
            assert leaf.relationship_turning_point.turning_point_id in turning_point_ids
        elif leaf.cost_incurred is not None:
            assert 1 <= leaf.cost_incurred.min_severity <= max(burdens)
        elif leaf.obligation_fulfilled is not None:
            assert any(burden >= leaf.obligation_fulfilled.min_burden for burden in burdens)
        elif leaf.stance_defended is not None:
            assert leaf.stance_defended.min_challenges >= 1
            assert leaf.stance_defended.min_cost_severity >= 1


def test_accepted_cost_relies_only_on_engine_derived_costs():
    source = compile_script_pack(PACK_DIR).source
    accepted_cost = next(
        requirement
        for requirement in source.completion_requirements
        if requirement.id == "accepted_cost"
    )
    assert accepted_cost.cost_incurred is not None
    assert accepted_cost.all is None and accepted_cost.any is None
