from __future__ import annotations

from typing import Any


def minimal_script_pack_dict() -> dict[str, Any]:
    return {
        "schema_version": "1.0",
        "identity": {
            "id": "test_pack",
            "title": "Test Pack",
            "language": "en",
            "genres": ["mystery"],
            "expected_minutes": 60,
        },
        "experience": {
            "viewpoint": "first_person",
            "prose_style": "concise",
            "tone": "quiet mystery",
            "choice_density": "key_moments",
            "min_scenes": 8,
            "max_scenes": 20,
        },
        "protagonist": {
            "id": "protagonist",
            "name": "Ren",
            "personality": {
                "traits": ["observant"],
                "values": ["honesty"],
                "flaws": ["hesitant"],
            },
            "background": "A new student",
            "capabilities": ["ask", "observe"],
            "boundaries": {"cannot": ["use violence"]},
        },
        "world": {
            "premise": "A notebook disappeared.",
            "immutable_rules": ["Dead characters cannot return."],
            "locations": [{"id": "cafe", "name": "Cafe", "tags": ["public"]}],
            "factions": [],
            "initial_situation": {
                "location": "cafe",
                "present_characters": ["alice"],
                "known_facts": ["cafe_is_open"],
            },
        },
        "characters": [
            {
                "id": "alice",
                "name": "Alice",
                "public_profile": "An outgoing student.",
                "personality": {
                    "traits": ["outgoing"],
                    "values": ["friendship"],
                    "fears": ["abandonment"],
                    "flaws": ["impulsive"],
                },
                "voice": {
                    "style": "direct",
                    "forbidden": ["formal speeches"],
                },
                "drives": ["find an ally"],
                "knowledge": ["cafe_is_open"],
                "secrets": ["who_took_notebook"],
                "capabilities": ["ask", "support"],
                "initial_relationship": {"trust": 35, "affection": 5},
            }
        ],
        "facts": {
            "fixed": [
                {
                    "id": "cafe_is_open",
                    "statement": "The cafe is open.",
                    "known_by": ["alice"],
                    "visibility": "revealed",
                }
            ],
            "latent_questions": [
                {
                    "id": "who_took_notebook",
                    "question": "Who took the notebook?",
                    "selection": "lazy_commit",
                    "candidates": [
                        {"value": "alice", "weight": 1.0, "requirements": []},
                        {"value": "stranger", "weight": 1.0, "requirements": []},
                    ],
                    "commit_when": [
                        "first_irreversible_evidence",
                        "explicit_revelation",
                    ],
                    "evidence_required": 1,
                }
            ],
            "derived": [
                {
                    "id": "alice_trusts_player",
                    "condition": "relationships.alice.trust >= 70",
                }
            ],
        },
        "goals": [
            {
                "id": "alice_find_ally",
                "owner": "alice",
                "desire": "Find an ally.",
                "urgency": 0.7,
                "conflicts_with": [],
                "success_condition": "relationships.alice.trust >= 70",
                "failure_condition": "relationships.alice.trust <= 10",
            }
        ],
        "interaction_rules": {
            "enabled_standard": ["ask", "observe", "support", "challenge"],
            "disabled": [],
            "extensions": [],
        },
        "endings": [
            {
                "id": "ally_ending",
                "title": "Together",
                "type": "hopeful",
                "priority": 80,
                "eligibility": {
                    "all": ["relationships.alice.trust >= 70"],
                    "any": [],
                    "none": [],
                },
                "required_outcomes": ["Alice and the protagonist cooperate."],
                "forbidden_outcomes": ["Alice becomes the mastermind."],
                "closing_tone": "hopeful",
            },
            {
                "id": "truth_ending",
                "title": "Truth",
                "type": "neutral",
                "priority": 70,
                "eligibility": {
                    "all": ["facts.who_took_notebook.truth_status == 'committed'"],
                    "any": [],
                    "none": [],
                },
                "required_outcomes": ["Explain the notebook truth."],
                "forbidden_outcomes": [],
                "closing_tone": "reflective",
            },
            {
                "id": "distance_ending",
                "title": "Distance",
                "type": "bittersweet",
                "priority": 60,
                "eligibility": {
                    "all": ["relationships.alice.trust <= 20"],
                    "any": [],
                    "none": [],
                },
                "required_outcomes": ["Alice and the protagonist part."],
                "forbidden_outcomes": [],
                "closing_tone": "bittersweet",
            },
            {
                "id": "fallback_ending",
                "title": "Closing Time",
                "type": "fallback",
                "priority": 1,
                "eligibility": {
                    "all": ["session.scene_count >= 17"],
                    "any": [],
                    "none": [],
                },
                "required_outcomes": ["Close the current conflict."],
                "forbidden_outcomes": [],
                "closing_tone": "quiet",
            },
        ],
        "assets": {},
    }


def minimal_pack_v2_dict() -> dict[str, Any]:
    """Return a minimal valid v2.0 script pack dict for testing.

    This pack has completion_requirements instead of endings,
    world_setting/story_history/opening_state instead of world.
    """
    return {
        "schema_version": "2.0",
        "identity": {
            "id": "test_pack_v2",
            "title": "Test Pack V2",
            "language": "en",
            "genres": ["mystery"],
            "expected_minutes": 60,
        },
        "experience": {
            "viewpoint": "first_person",
            "prose_style": "concise",
            "tone": "quiet mystery",
            "choice_density": "key_moments",
            "min_scenes": 8,
            "max_scenes": 20,
        },
        "protagonist": {
            "id": "protagonist",
            "name": "Ren",
            "personality": {
                "traits": ["observant"],
                "values": ["honesty"],
                "flaws": ["hesitant"],
            },
            "background": "A new student.",
            "capabilities": ["ask", "observe"],
            "boundaries": {"cannot": ["use violence"]},
        },
        "world_setting": {
            "premise": "A notebook disappeared from the cafe.",
            "immutable_rules": ["Death is irreversible."],
            "locations": [
                {"id": "cafe", "name": "Cafe", "tags": ["public"]},
                {"id": "back_alley", "name": "Back Alley", "tags": ["quiet"]},
            ],
            "factions": [],
            "forbidden_content": ["explicit violence"],
            "fact_rules": ["No supernatural powers."],
        },
        "story_history": {
            "summary": "Alice lost her notebook containing sensitive information.",
            "events": [
                {
                    "summary": "Alice brought her notebook to the cafe.",
                    "participants": ("alice",),
                },
                {
                    "summary": "The notebook went missing.",
                    "participants": ("alice", "bob"),
                    "remembered_differently_by": {
                        "alice": "She left it on the table.",
                    },
                },
            ],
        },
        "opening_state": {
            "location": "cafe",
            "present_characters": ["alice"],
            "known_facts": ["cafe_is_open"],
            "time_label": "Saturday afternoon",
            "starting_pressure": 0.15,
        },
        "characters": [
            {
                "id": "alice",
                "name": "Alice",
                "public_profile": "An outgoing student.",
                "personality": {
                    "traits": ["outgoing"],
                    "values": ["friendship"],
                    "fears": ["abandonment"],
                    "flaws": ["impulsive"],
                },
                "voice": {
                    "style": "direct",
                    "forbidden": ["formal speeches"],
                },
                "drives": ["find an ally"],
                "knowledge": ["cafe_is_open"],
                "secrets": ["who_took_notebook"],
                "capabilities": ["ask", "support"],
                "initial_relationship": {"trust": 35, "affection": 5},
            },
            {
                "id": "bob",
                "name": "Bob",
                "public_profile": "A cautious researcher.",
                "personality": {
                    "traits": ["cautious"],
                    "values": ["evidence"],
                    "fears": ["repeating old mistakes"],
                    "flaws": ["controlling"],
                },
                "voice": {
                    "style": "precise",
                    "forbidden": ["casual speculation"],
                },
                "drives": ["verify the evidence"],
                "knowledge": ["cafe_is_open"],
                "secrets": [],
                "capabilities": ["ask", "observe", "challenge"],
                "initial_relationship": {"trust": 25, "affection": 0},
            },
        ],
        "facts": {
            "fixed": [
                {
                    "id": "cafe_is_open",
                    "statement": "The cafe is open.",
                    "known_by": ["alice", "bob"],
                    "visibility": "revealed",
                }
            ],
            "latent_questions": [
                {
                    "id": "who_took_notebook",
                    "question": "Who took the notebook?",
                    "selection": "lazy_commit",
                    "candidates": [
                        {"value": "alice", "weight": 1.0, "requirements": []},
                        {"value": "stranger", "weight": 1.0, "requirements": []},
                    ],
                    "commit_when": [
                        "first_irreversible_evidence",
                        "explicit_revelation",
                    ],
                    "evidence_required": 1,
                }
            ],
            "derived": [
                {
                    "id": "alice_trusts_player",
                    "condition": "relationships.alice.trust >= 70",
                }
            ],
        },
        "goals": [
            {
                "id": "alice_find_ally",
                "owner": "alice",
                "desire": "Find an ally.",
                "urgency": 0.7,
                "conflicts_with": [],
                "success_condition": "relationships.alice.trust >= 70",
                "failure_condition": "relationships.alice.trust <= 10",
            }
        ],
        "completion_requirements": [
            {
                "id": "understand_truth",
                "description": "Player must understand who took the notebook.",
                "fact_revealed": {"fact_id": "who_took_notebook"},
            },
            {
                "id": "build_trust",
                "description": "Player must earn Alice's trust.",
                "relationship_turning_point": {"turning_point_id": "alice_mutual_trust"},
            },
        ],
        "conflict_axes": [
            {
                "id": "trust_vs_evidence",
                "values": ["trust", "evidence"],
                "source_character_ids": ["alice", "bob"],
                "initial_incompatibility": (
                    "Alice needs personal trust while Bob requires verifiable evidence."
                ),
            }
        ],
        "relationship_event_tags": [
            {"id": "public_trust", "description": "Trusted someone in public."},
            {"id": "accepted_truth", "description": "Accepted an inconvenient truth."},
        ],
        "relationship_turning_points": [
            {
                "id": "alice_mutual_trust",
                "character_id": "alice",
                "all_of_event_tags": ["public_trust", "accepted_truth"],
                "min_distinct_source_choices": 2,
            }
        ],
        "obligation_kinds": [
            {
                "id": "keep_secret",
                "description": "Keep a disclosed secret.",
                "burden": 2,
                "allowed_outcomes": ["fulfilled", "broken", "released"],
            }
        ],
        "interaction_rules": {
            "enabled_standard": ["ask", "observe", "support", "challenge"],
            "disabled": [],
            "extensions": [],
        },
        "assets": {},
    }
