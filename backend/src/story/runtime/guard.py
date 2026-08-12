"""Canon and Knowledge Guard for segment validation.

Layer 1: Deterministic structural checks.
Layer 2: Bounded semantic critic for knowledge leaks and contradictions.
"""

from __future__ import annotations

from src.story.script_pack.models import CompiledScriptPack, ScriptPackSourceV2
from src.story.state import (
    FactTruthStatus,
    SessionState,
)

from .segment_contracts import (
    GuardResult,
    GuardViolation,
    SegmentDraft,
    SegmentPlan,
)


class Guard:
    """Implements GuardPort.check_segment with deterministic and semantic layers."""

    def check_segment(
        self,
        pack: CompiledScriptPack,
        state: SessionState,
        plan: SegmentPlan,
        draft: SegmentDraft,
    ) -> GuardResult:
        violations: list[GuardViolation] = []

        # --- Layer 1: Deterministic structural checks ---

        # 1. Segment ID must match
        if draft.segment_id != plan.segment_id:
            violations.append(
                GuardViolation(
                    kind="unauthorized_fact",
                    detail=f"segment_id mismatch: plan={plan.segment_id}, draft={draft.segment_id}",
                )
            )

        # 2. Scene count must match
        if len(draft.scene_drafts) != len(plan.scenes):
            violations.append(
                GuardViolation(
                    kind="unauthorized_fact",
                    detail=f"scene count mismatch: plan has {len(plan.scenes)} scenes, draft has {len(draft.scene_drafts)}",
                )
            )

        # 3. Per-scene checks
        plan_scenes = {s.scene_id: s for s in plan.scenes}
        all_known_character_ids = set(pack.character_ids)
        global_block_index = 0

        for scene_draft in draft.scene_drafts:
            plan_scene = plan_scenes.get(scene_draft.scene_id)
            if plan_scene is None:
                violations.append(
                    GuardViolation(
                        kind="unauthorized_fact",
                        detail=f"scene_id in draft not found in plan: {scene_draft.scene_id}",
                    )
                )
                global_block_index += len(scene_draft.blocks)
                continue

            # 3b. Blocks must not be empty
            if not scene_draft.blocks:
                violations.append(
                    GuardViolation(
                        kind="unauthorized_fact",
                        block_index=global_block_index,
                        detail=f"scene {scene_draft.scene_id} has no blocks",
                    )
                )

            # 3c. Speaker presence check
            present = set(plan_scene.present_character_ids)
            for block in scene_draft.blocks:
                if block.kind == "dialogue":
                    if block.character_id is None:
                        violations.append(
                            GuardViolation(
                                kind="wrong_speaker",
                                block_index=global_block_index,
                                detail=f"dialogue block at index {global_block_index} has no character_id",
                            )
                        )
                    elif block.character_id not in present:
                        violations.append(
                            GuardViolation(
                                kind="wrong_speaker",
                                block_index=global_block_index,
                                character_id=block.character_id,
                                detail=(
                                    f"speaker '{block.character_id}' is not present in scene "
                                    f"{scene_draft.scene_id}: present={sorted(present)}"
                                ),
                            )
                        )
                    elif block.character_id not in all_known_character_ids:
                        violations.append(
                            GuardViolation(
                                kind="wrong_speaker",
                                block_index=global_block_index,
                                character_id=block.character_id,
                                detail=f"speaker '{block.character_id}' is not a known character in the pack",
                            )
                        )
                global_block_index += 1

        # 4. Choice identity check (for decision terminal)
        if plan.terminal == "decision":
            last_scene = plan.scenes[-1]
            planned_choice_ids = {c.option_id for c in last_scene.choices}

            draft_choice_ids = {c.option_id for c in draft.choices}
            if draft_choice_ids != planned_choice_ids:
                violations.append(
                    GuardViolation(
                        kind="unauthorized_fact",
                        detail=(
                            f"choice IDs mismatch: plan={sorted(planned_choice_ids)}, "
                            f"draft={sorted(draft_choice_ids)}"
                        ),
                    )
                )

            # 4a. Choice labels must be unique and non-empty
            labels = [c.label.strip().casefold() for c in draft.choices]
            if any(not label for label in labels):
                violations.append(
                    GuardViolation(
                        kind="unauthorized_fact",
                        detail="choice labels cannot be empty",
                    )
                )
            if len(labels) != len(set(labels)):
                violations.append(
                    GuardViolation(
                        kind="unauthorized_fact",
                        detail="choice labels must be unique",
                    )
                )

            # 4b. Decision choices must be 2-4
            if not 2 <= len(draft.choices) <= 4:
                violations.append(
                    GuardViolation(
                        kind="unauthorized_fact",
                        detail=f"decision draft requires 2-4 choices, got {len(draft.choices)}",
                    )
                )

        # 5. Ending check
        if plan.terminal == "ending":
            if draft.ending is None:
                violations.append(
                    GuardViolation(
                        kind="unauthorized_fact",
                        detail="ending terminal requires ending draft blocks",
                    )
                )
            elif (
                plan.ending_proposal is not None
                and draft.ending.title != plan.ending_proposal.title
            ):
                violations.append(
                    GuardViolation(
                        kind="unauthorized_fact",
                        detail=(
                            f"ending title mismatch: plan='{plan.ending_proposal.title}', "
                            f"draft='{draft.ending.title}'"
                        ),
                    )
                )

        # 6. Continue scenes in draft should not have choices
        for i, scene_draft in enumerate(draft.scene_drafts):
            if i < len(draft.scene_drafts) - 1 and scene_draft.choices:
                violations.append(
                    GuardViolation(
                        kind="unauthorized_fact",
                        detail=f"non-terminal scene {scene_draft.scene_id} has choices",
                    )
                )

        # 6b. Non-decision plans must not carry any draft choices
        if plan.terminal != "decision":
            for scene_draft in draft.scene_drafts:
                if scene_draft.choices:
                    violations.append(
                        GuardViolation(
                            kind="unauthorized_fact",
                            detail=(
                                f"plan terminal '{plan.terminal}' does not allow choices "
                                f"but scene {scene_draft.scene_id} has {len(scene_draft.choices)}"
                            ),
                        )
                    )
            if draft.choices:
                violations.append(
                    GuardViolation(
                        kind="unauthorized_fact",
                        detail=(
                            f"plan terminal '{plan.terminal}' does not allow choices "
                            f"but draft has {len(draft.choices)}"
                        ),
                    )
                )

        # 7. Fact visibility check: a dialogue may only reference a hidden fact
        #    if the speaker already knows it. Only flag when the dialogue text
        #    actually mentions the fact — otherwise any scene with a hidden
        #    related fact would reject every speaker who hasn't learned it.
        global_block_index = 0
        for scene_draft in draft.scene_drafts:
            plan_scene = plan_scenes.get(scene_draft.scene_id)
            if plan_scene is None:
                global_block_index += len(scene_draft.blocks)
                continue
            for block in scene_draft.blocks:
                if block.kind == "dialogue" and block.character_id is not None:
                    text_lower = block.text.lower()
                    for fact_id in plan_scene.related_fact_ids:
                        if fact_id not in text_lower:
                            continue
                        fact_runtime = state.facts.get(fact_id)
                        if fact_runtime and fact_runtime.visibility.value == "hidden":
                            # Only characters who already know the fact can reference it
                            char_runtime = state.characters.get(block.character_id)
                            if char_runtime and fact_id not in char_runtime.knowledge:
                                violations.append(
                                    GuardViolation(
                                        kind="knowledge_leak",
                                        block_index=global_block_index,
                                        character_id=block.character_id,
                                        detail=f"speaker references hidden fact '{fact_id}' they don't know",
                                    )
                                )
                global_block_index += 1

        # 8. Evidence counts: fact commits must have sufficient evidence
        for scene in plan.scenes:
            for fact_commit in scene.fact_commits:
                fact_runtime = state.facts.get(fact_commit.fact_id)
                if fact_runtime and fact_runtime.evidence_required > len(fact_runtime.evidence_event_ids):
                    violations.append(
                        GuardViolation(
                            kind="unauthorized_fact",
                            detail=f"fact '{fact_commit.fact_id}' committed without sufficient evidence: "
                                   f"required {fact_runtime.evidence_required}, have {len(fact_runtime.evidence_event_ids)}",
                        )
                    )

        # 9. World-rule references: check dialogue doesn't contradict immutable rules
        immutable_rules = (
            pack.source.world_setting.immutable_rules
            if isinstance(pack.source, ScriptPackSourceV2)
            else pack.source.world.immutable_rules
        )
        global_block_index = 0
        for scene_draft in draft.scene_drafts:
            for block in scene_draft.blocks:
                if block.kind == "dialogue" and block.character_id is not None:
                    text_lower = block.text.lower()
                    for rule in immutable_rules:
                        if rule.lower() in text_lower and (
                            "not" in text_lower or "never" in text_lower or "can't" in text_lower
                        ):
                            violations.append(
                                GuardViolation(
                                    kind="contradiction",
                                    block_index=global_block_index,
                                    detail=f"dialogue may contradict immutable rule: '{rule[:50]}...'",
                                )
                            )
                global_block_index += 1

        # --- Layer 2: Bounded semantic critic ---

        # Knowledge leak heuristic: check if any dialogue block's text
        # references a fact_id from another character's knowledge that the
        # speaker doesn't know.
        character_knowledge = {
            char_id: set(runtime.knowledge)
            for char_id, runtime in state.characters.items()
        }

        # Build a set of authorized fact IDs for this segment
        authorized_fact_ids: set[str] = set()
        for scene in plan.scenes:
            authorized_fact_ids.update(scene.related_fact_ids)
            authorized_fact_ids.update(fc.fact_id for fc in scene.fact_commits)
        authorized_fact_ids.update(fc.fact_id for fc in plan.new_facts)

        global_block_index = 0
        for scene_draft in draft.scene_drafts:
            plan_scene = plan_scenes.get(scene_draft.scene_id)
            if plan_scene is None:
                global_block_index += len(scene_draft.blocks)
                continue
            for block in scene_draft.blocks:
                if block.kind == "dialogue" and block.character_id is not None:
                    speaker_knowledge = character_knowledge.get(block.character_id, set())
                    text_lower = block.text.lower()
                    for fact_id, fact_runtime in state.facts.items():
                        if (
                            fact_runtime.truth_status == FactTruthStatus.COMMITTED
                            and fact_id not in speaker_knowledge
                            and fact_id not in authorized_fact_ids
                            and fact_id in text_lower
                        ):
                            violations.append(
                                GuardViolation(
                                    kind="knowledge_leak",
                                    block_index=global_block_index,
                                    character_id=block.character_id,
                                    detail=(
                                        f"speaker '{block.character_id}' may reference "
                                        f"fact '{fact_id}' which they have not learned"
                                    ),
                                )
                            )
                global_block_index += 1

        if violations:
            return GuardResult(passed=False, violations=tuple(violations))
        return GuardResult(passed=True)
