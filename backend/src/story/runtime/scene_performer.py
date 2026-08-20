"""Scene Performance Loop: one model call per scene, block-level repair.

The scene is the generation/validation/repair unit (P2): a bad block costs a
targeted rewrite, not a whole-segment reshuffle.  The segment stays the
commit unit — assembly happens after every scene passes, and the existing
whole-segment checks (guard/density/repetition/judge) still run at the
segment boundary.

The seam anchor is structural: scene n+1's brief carries scene n's approved
final blocks verbatim, so time-regression between scenes ("almost closing"
→ "bright afternoon") loses its structural footing.
"""

from __future__ import annotations

from typing import Any, Protocol

from pydantic import Field

from src.story.runtime.contracts import (
    ModelContractError,
    SceneDraft,
    WrittenChoice,
)
from src.story.runtime.drama_manager import beat_briefs
from src.story.runtime.model import LLMClient
from src.story.runtime.segment_context import build_segment_writer_context
from src.story.runtime.segment_contracts import (
    LedgerUpdate,
    RuntimeModel,
    SegmentDraft,
    SegmentPlan,
)
from src.story.script_pack.models import CompiledScriptPack
from src.story.state import NarrativeBlock, PresentedChoice, SessionState

SCENE_PERFORMER_INSTRUCTIONS = """You are the Scene Performer for a constrained visual novel.
You perform EXACTLY ONE scene of an approved segment plan — the scene listed
under "this_scene". The other scenes are context only; never write them.

HARD RULES — the output is machine-validated and any violation is rejected:

1. SEAM (when the context has a "seam_tail" section):
   These are the literal final blocks of the scene directly before this one.
   Continue seamlessly from the last block — never repeat or re-narrate them,
   never move time backwards — and match quotation marks, punctuation, and
   formatting exactly so the seam is invisible.

2. KNOWLEDGE SCOPING:
   A character's dialogue may reference ONLY facts in that character's own
   "known_facts". Never let a speaker hint at a fact they have not learned.

3. NO INTERNAL IDS IN PROSE:
   Never write a fact/character/location/option id inside narration or
   dialogue. Refer to everything in natural language.

4. BEAT (when the context has a "beat" section):
   The scene's dramatic content is already decided — perform the beat's
   purpose and land every "must_include" line visibly, in natural language.
   Never substitute a different event.

5. CHOICES (when this_scene has "terminal": "decision"):
   Return one natural-language label per planned choice, with the EXACT same
   option_id. Never omit or invent option ids.

6. LEDGER (when the prose establishes durable details):
   Register entity attributes, prose promises, and motifs in ledger_updates
   with the documented kinds. Re-stating an already-recorded value needs no
   update; changing one is a continuity violation and will be rejected.

7. Write in the script pack language and prose style.
- Return only the requested structured contract."""


class ScenePerformanceOutput(RuntimeModel):
    """One performed scene: prose blocks plus its structured increments."""

    scene_id: str
    blocks: tuple[NarrativeBlock, ...] = Field(min_length=1)
    choices: tuple[WrittenChoice, ...] = ()
    ledger_updates: tuple[LedgerUpdate, ...] = ()


class ScenePerformerPort(Protocol):
    async def perform_scene(
        self,
        pack: CompiledScriptPack,
        state: SessionState,
        plan: SegmentPlan,
        scene_index: int,
        *,
        seam_tail: tuple[NarrativeBlock, ...] = (),
        pending_choice: PresentedChoice | None = None,
        rejection_notes: tuple[str, ...] = (),
    ) -> SceneDraft:
        ...

    async def repair_blocks(
        self,
        pack: CompiledScriptPack,
        state: SessionState,
        plan: SegmentPlan,
        scene_index: int,
        scene_draft: SceneDraft,
        block_indices: tuple[int, ...],
        instructions: tuple[str, ...],
    ) -> SceneDraft:
        ...


def build_scene_brief(
    pack: CompiledScriptPack,
    state: SessionState,
    plan: SegmentPlan,
    scene_index: int,
    *,
    seam_tail: tuple[NarrativeBlock, ...] = (),
    pending_choice: PresentedChoice | None = None,
    rejection_notes: tuple[str, ...] = (),
) -> dict[str, Any]:
    """Per-scene performance brief: full writer context + this scene's brief.

    The underlying writer context is segment-scoped (the performer must know
    the whole plan); ``this_scene``/``beat``/``seam_tail`` focus the call on
    exactly one scene.
    """
    scene = plan.scenes[scene_index]
    brief = build_segment_writer_context(
        pack, state, plan, pending_choice=pending_choice if scene_index == 0 else None
    )
    beat = next(
        (item for item in beat_briefs(pack, plan) if item["scene_id"] == scene.scene_id),
        None,
    )
    brief["this_scene"] = scene.model_dump(mode="json")
    if beat is not None:
        brief["beat"] = beat
    if scene.terminal == "ending":
        # The finale density contract, stated up front: this scene's blocks
        # ride the ending and must clear the floor or the segment rejects.
        from src.story.runtime.validator import ENDING_BLOCK_FLOOR

        brief["ending_block_floor"] = ENDING_BLOCK_FLOOR
    if seam_tail:
        brief["seam_tail"] = [block.model_dump(mode="json") for block in seam_tail]
    if rejection_notes:
        brief["rejection_notes"] = list(rejection_notes)
    return brief


def assemble_segment_draft(
    plan: SegmentPlan,
    scene_drafts: tuple[SceneDraft, ...],
) -> SegmentDraft:
    """Assemble per-scene performances into a segment draft."""
    choices = tuple(
        choice for draft in scene_drafts for choice in draft.choices
    )
    return SegmentDraft(
        segment_id=plan.segment_id,
        scene_drafts=scene_drafts,
        choices=choices,
    )


def block_targets(
    scene_drafts: tuple[SceneDraft, ...],
    global_indices: tuple[int, ...],
) -> dict[int, tuple[int, ...]]:
    """Map flattened block indices to (scene index, local block indices)."""
    targets: dict[int, tuple[int, ...]] = {}
    global_start = 0
    for scene_index, draft in enumerate(scene_drafts):
        local = tuple(
            index - global_start
            for index in global_indices
            if global_start <= index < global_start + len(draft.blocks)
        )
        if local:
            targets[scene_index] = local
        global_start += len(draft.blocks)
    return targets


def seam_tail_blocks(
    scene_drafts: tuple[SceneDraft, ...],
    scene_index: int,
    *,
    max_blocks: int = 3,
) -> tuple[NarrativeBlock, ...]:
    """The previous scene's approved final blocks, verbatim."""
    if scene_index == 0:
        return ()
    previous = scene_drafts[scene_index - 1]
    return previous.blocks[-max_blocks:]


class LLMScenePerformer:
    """Scene Performer backed by the structured-output LLM client."""

    def __init__(self, client: LLMClient) -> None:
        self.client = client

    async def perform_scene(
        self,
        pack: CompiledScriptPack,
        state: SessionState,
        plan: SegmentPlan,
        scene_index: int,
        *,
        seam_tail: tuple[NarrativeBlock, ...] = (),
        pending_choice: PresentedChoice | None = None,
        rejection_notes: tuple[str, ...] = (),
    ) -> SceneDraft:
        brief = build_scene_brief(
            pack,
            state,
            plan,
            scene_index,
            seam_tail=seam_tail,
            pending_choice=pending_choice,
            rejection_notes=rejection_notes,
        )
        output = await self.client.complete_structured(
            instructions=SCENE_PERFORMER_INSTRUCTIONS,
            payload={"operation": "perform_scene", "context": brief},
            output_type=ScenePerformanceOutput,
        )
        return self._checked_draft(plan, scene_index, output)

    async def repair_blocks(
        self,
        pack: CompiledScriptPack,
        state: SessionState,
        plan: SegmentPlan,
        scene_index: int,
        scene_draft: SceneDraft,
        block_indices: tuple[int, ...],
        instructions: tuple[str, ...],
    ) -> SceneDraft:
        brief = build_scene_brief(pack, state, plan, scene_index)
        brief["repair"] = {
            "rewrite_blocks": list(block_indices),
            "reasons": list(instructions),
            "keep_blocks": [
                index
                for index in range(len(scene_draft.blocks))
                if index not in block_indices
            ],
            "current_blocks": [block.model_dump(mode="json") for block in scene_draft.blocks],
        }
        output = await self.client.complete_structured(
            instructions=SCENE_PERFORMER_INSTRUCTIONS
            + "\n\n8. REPAIR: rewrite ONLY the blocks listed in \"repair\".\"rewrite_blocks\","
            + " keeping every other block byte-identical. The reasons name what"
            + " each rewrite must fix.",
            payload={"operation": "repair_blocks", "context": brief},
            output_type=ScenePerformanceOutput,
        )
        return self._checked_draft(plan, scene_index, output)

    def _checked_draft(
        self,
        plan: SegmentPlan,
        scene_index: int,
        output: ScenePerformanceOutput,
    ) -> SceneDraft:
        scene = plan.scenes[scene_index]
        if output.scene_id != scene.scene_id:
            raise ModelContractError(
                f"performer changed scene_id: expected {scene.scene_id}, "
                f"got {output.scene_id}"
            )
        return SceneDraft(
            scene_id=scene.scene_id,
            blocks=output.blocks,
            choices=output.choices,
        )
