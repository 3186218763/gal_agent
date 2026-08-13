"""Independent Semantic Judge for proposed segment content (ADR 0007).

The judge is separate from the generating agent, reports structured
findings, and has no authority to write prose or mutate state.  High-risk
meaning conflicts (canon contradiction, knowledge leakage, Choice Meaning
reversal, Work Boundary violations, missing Ending Integrity) fail closed
when safety cannot be established; softer concerns (voice, style, pacing)
inform quality without blocking every turn.
"""

from __future__ import annotations

import json
from typing import Any, Literal, Protocol

from agents import Agent
from agents.models.openai_responses import OpenAIResponsesModel

from src.story.runtime.contracts import RuntimeModel
from src.story.runtime.model import ProviderStrictOutputSchema, run_with_contract_retry
from src.story.runtime.segment_context import (
    _completion_requirement_views,
    _event_trace_digest,
    _get_forbidden_content,
    _get_immutable_rules,
    _get_world_setting,
)
from src.story.runtime.segment_contracts import SegmentDraft, SegmentPlan
from src.story.script_pack.models import CompiledScriptPack
from src.story.state import PresentedChoice, SessionState

# ---------------------------------------------------------------------------
# Structured findings contract
# ---------------------------------------------------------------------------

BLOCKING_KINDS = frozenset(
    {
        "canon_contradiction",
        "knowledge_leakage",
        "choice_reversal",
        "boundary_violation",
        "missing_ending_integrity",
    }
)


class JudgeFinding(RuntimeModel):
    kind: Literal[
        "canon_contradiction",
        "knowledge_leakage",
        "choice_reversal",
        "boundary_violation",
        "missing_ending_integrity",
        "voice",
        "style",
        "pacing",
    ]
    severity: Literal["blocking", "informational"]
    block_index: int | None = None
    character_id: str | None = None
    detail: str


class JudgeFindings(RuntimeModel):
    findings: tuple[JudgeFinding, ...] = ()

    @property
    def blocking(self) -> tuple[JudgeFinding, ...]:
        return tuple(finding for finding in self.findings if finding.severity == "blocking")

    @property
    def passed(self) -> bool:
        """True only when no blocking finding was reported."""
        return not self.blocking


class SemanticJudgePort(Protocol):
    async def judge_segment(
        self,
        pack: CompiledScriptPack,
        state: SessionState,
        plan: SegmentPlan,
        draft: SegmentDraft,
        pending_choice: PresentedChoice | None = None,
    ) -> JudgeFindings: ...


# ---------------------------------------------------------------------------
# Context builder
# ---------------------------------------------------------------------------


def build_judge_context(
    pack: CompiledScriptPack,
    state: SessionState,
    plan: SegmentPlan,
    draft: SegmentDraft,
    pending_choice: PresentedChoice | None = None,
) -> dict[str, Any]:
    """Read-only context: canon, committed history, and the proposed content.

    The context includes the committed Choice Meaning being resolved (when
    present) so the judge can verify the consequence does not reverse what
    the player chose.  It never includes a request to rewrite anything.
    """
    source = pack.source
    world_setting = _get_world_setting(source)
    return {
        "pack": {
            "id": source.identity.id,
            "language": source.identity.language,
            "premise": world_setting.premise,
            "immutable_rules": _get_immutable_rules(source),
            "forbidden_content": _get_forbidden_content(source),
            "protagonist_id": source.protagonist.id,
        },
        "world_truth": {
            "location_id": state.world.location_id,
            "phase": state.world.phase.value,
            "scene_count": state.world.scene_count,
            "pressure": state.world.pressure,
            "present_character_ids": list(state.world.present_character_ids),
        },
        "completion_requirements": _completion_requirement_views(pack, state),
        "event_trace": _event_trace_digest(state),
        "pending_choice_meaning": (
            pending_choice.model_dump(mode="json") if pending_choice is not None else None
        ),
        "proposed_segment": {
            "segment_id": plan.segment_id,
            "terminal": plan.terminal,
            "scenes": [
                {
                    "scene_id": scene.scene_id,
                    "summary": scene.summary,
                    "location_id": scene.location_id,
                    "present_character_ids": list(scene.present_character_ids),
                    "terminal": scene.terminal,
                    "choices": [choice.model_dump(mode="json") for choice in scene.choices],
                }
                for scene in plan.scenes
            ],
            "ending_proposal": (
                plan.ending_proposal.model_dump(mode="json")
                if plan.ending_proposal is not None
                else None
            ),
            "blocks": [
                {
                    "block_index": index,
                    "scene_id": scene_draft.scene_id,
                    "kind": block.kind,
                    "text": block.text,
                    "character_id": block.character_id,
                }
                for scene_draft in draft.scene_drafts
                for index, block in enumerate(scene_draft.blocks)
            ],
        },
    }


JUDGE_INSTRUCTIONS = """You are the Independent Semantic Judge for a constrained visual novel.

Your only job is to REPORT STRUCTURED FINDINGS about the proposed segment.
You cannot write prose, rewrite dialogue, change the plan, or mutate state.
You return a list of findings; an empty list means the segment is safe.

═══ BLOCKING KINDS (fail closed when any is certain) ═══

1. canon_contradiction — the proposed segment contradicts committed history
   in the event_trace or the pack's immutable_rules: an established fact,
   a relationship direction, a resolved dramatic question, or an ending
   requirement is denied or silently changed.
2. knowledge_leakage — a character states, hints at, or relies on a fact or
   belief they cannot have learned from the committed event_trace.
3. choice_reversal — the segment makes the player's committed Choice Meaning
   (see pending_choice_meaning) irrelevant, ignored, or reversed: the intent
   or stance is contradicted, the accepted risk or obligation is erased, or
   the consequence pretends a different choice was made.
4. boundary_violation — the segment crosses an authored boundary: forbidden
   content, a protagonist capability the pack does not grant, a location or
   character not present, or an ending that ignores the completion contract.
5. missing_ending_integrity — for an ending segment: the proposed ending
   does not acknowledge the committed dramatic development, abandons open
   obligations without reason, or its title/summary contradicts the tone of
   the history it must conclude.

Mark a finding blocking ONLY when you are certain.  If you cannot establish
safety from the provided context, prefer a blocking finding for the
high-risk kinds above (fail closed).  Do not guess facts that are absent —
absence of evidence is not a contradiction.

═══ INFORMATIONAL KINDS (never block) ═══

- voice: a character's dialogue does not fit their established voice.
- style: prose deviates from the pack's stated prose style or tone.
- pacing: the segment feels rushed, padded, or off-pace.

For every finding, set block_index to the first block index it applies to
(0-based, matching the blocks array) when applicable, character_id when a
specific character is implicated, and a one-sentence detail in the script
pack language.

Return only the requested structured contract."""


class SdkSemanticJudge:
    """Independent Semantic Judge backed by the OpenAI Agents SDK.

    Fail closed: any judge failure (network, contract, or blocking finding)
    is surfaced to the caller as ``RuntimeGenerationUnavailable`` — a
    segment is never committed because the judge could not establish safety.
    """

    def __init__(
        self,
        model: OpenAIResponsesModel,
        instructions: str = JUDGE_INSTRUCTIONS,
    ) -> None:
        self.agent = Agent(
            name="Semantic Judge",
            instructions=instructions,
            model=model,
            output_type=ProviderStrictOutputSchema(JudgeFindings),
        )

    async def judge_segment(
        self,
        pack: CompiledScriptPack,
        state: SessionState,
        plan: SegmentPlan,
        draft: SegmentDraft,
        pending_choice: PresentedChoice | None = None,
    ) -> JudgeFindings:
        prompt = json.dumps(
            {
                "operation": "judge_segment",
                "context": build_judge_context(pack, state, plan, draft, pending_choice),
            },
            ensure_ascii=False,
        )
        return await run_with_contract_retry(self.agent, prompt, JudgeFindings)


__all__ = [
    "BLOCKING_KINDS",
    "JUDGE_INSTRUCTIONS",
    "JudgeFinding",
    "JudgeFindings",
    "SdkSemanticJudge",
    "SemanticJudgePort",
    "build_judge_context",
]
