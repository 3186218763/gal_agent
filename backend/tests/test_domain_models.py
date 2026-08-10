# backend/tests/test_domain_models.py
from src.domain.enums import Phase, EndingType, GoalStatus, GoalType, EventType
from src.domain.setting_pack import SettingPack, CharacterDef, GoalDef, EndingDef
from src.domain.world_state import WorldState, GoalRuntime, RelationshipState
from src.domain.events import EventDatabase, GameEvent
from src.domain.enums import EventType, GoalStatus, Phase
from src.domain.options import ChoiceOption, PredictedConsequences


def test_phase_order():
    assert [p.value for p in Phase] == ["setup", "rising", "climax", "falling"]


def test_ending_types_include_fallback():
    assert EndingType.FALLBACK.value == "fallback"
    assert EndingType.VICTORY.value == "victory"


def test_setting_pack_minimal():
    pack = SettingPack(
        pack_id="t",
        title="T",
        premise="p",
        characters=[
            CharacterDef(id="alice", name="Alice", personality="curious")
        ],
        goals=[GoalDef(id="g1", title="G", description="d")],
        endings=[
            EndingDef(
                id="e1",
                title="E",
                condition="steps >= 1",
                type="fallback",
                priority=1,
                content="end",
            )
        ],
        opening_seed="seed",
    )
    assert pack.max_steps == 24
    assert pack.characters[0].initial_relationship.trust == 50


def test_world_state_from_pack_defaults():
    # helper will be world_state.initial_world_state(pack, session_id)
    from src.domain.world_state import initial_world_state

    pack = SettingPack(
        pack_id="t",
        title="T",
        premise="p",
        characters=[
            CharacterDef(id="alice", name="Alice", personality="x", initial_relationship={"trust": 40, "romance": 0})
        ],
        goals=[GoalDef(id="g1", title="G", description="d")],
        endings=[
            EndingDef(id="e1", title="E", condition="steps >= 99", type="fallback", priority=1, content="end")
        ],
        opening_seed="seed",
        initial_flags={"game_started": False},
    )
    state = initial_world_state(pack, session_id="s1")
    assert state.session_id == "s1"
    assert state.phase == Phase.SETUP
    assert state.relationships["alice"].trust == 40
    assert state.goal_progress["g1"].status == GoalStatus.ACTIVE
    assert state.flags["game_started"] is False


def test_event_database_append_only():
    db = EventDatabase()
    e = GameEvent(id="1", step=0, type=EventType.NARRATION, payload={"content": "hi"})
    db.append(e)
    assert len(db.list()) == 1
    assert db.recent(1)[0].payload["content"] == "hi"


def test_choice_option_fingerprint_fields():
    opt = ChoiceOption(
        text="听她说完",
        stance="cautious",
        predicted_consequences=PredictedConsequences(
            flag_changes={"listened": True},
            relationship_deltas={"alice": {"trust": 5}},
        ),
        narrative_preview="她松了口气",
    )
    assert opt.text
    assert opt.predicted_consequences.flag_changes["listened"] is True

