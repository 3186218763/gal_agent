"""V2 runtime contracts and ports for Planner/Writer and segment pipeline."""

from .config import ConfigurationError, OpenCodeGoSettings
from .contracts import (
    ActionResolution,
    ChoicePlan,
    DirectorOutput,
    EndingDraft,
    EndingProposal,
    FactCommitPlan,
    GoalDelta,
    ModelContractError,
    PacingEnvelope,
    PlannerOutput,
    PlannerPort,
    RelationshipDelta,
    RuntimeModel,
    SceneDraft,
    ScenePlan,
    SegmentDraft,
    SegmentPlan,
    SegmentWriterOutput,
    WriterOutput,
    WriterPort,
    WrittenChoice,
)
from .director import SdkDirector
from .guard import Guard
from .model import ModelBundle, build_model_bundle, run_with_contract_retry
from .planner import SdkPlanner
from .segment_context import build_director_context, build_segment_writer_context
from .segment_contracts import (
    DirectorPort,
    GuardPort,
    GuardResult,
    GuardViolation,
    SegmentWriterPort,
    ThreadOperation,
)
from .segment_writer import SdkSegmentWriter
from .writer import SdkWriter

__all__ = [
    "ActionResolution",
    "ChoicePlan",
    "ConfigurationError",
    "DirectorOutput",
    "DirectorPort",
    "EndingDraft",
    "EndingProposal",
    "FactCommitPlan",
    "GoalDelta",
    "Guard",
    "GuardPort",
    "GuardResult",
    "GuardViolation",
    "ModelBundle",
    "ModelContractError",
    "OpenCodeGoSettings",
    "PacingEnvelope",
    "PlannerOutput",
    "PlannerPort",
    "RelationshipDelta",
    "RuntimeModel",
    "SceneDraft",
    "ScenePlan",
    "SdkDirector",
    "SdkPlanner",
    "SdkSegmentWriter",
    "SdkWriter",
    "SegmentDraft",
    "SegmentPlan",
    "SegmentWriterOutput",
    "SegmentWriterPort",
    "ThreadOperation",
    "WriterOutput",
    "WriterPort",
    "WrittenChoice",
    "build_director_context",
    "build_model_bundle",
    "build_segment_writer_context",
    "run_with_contract_retry",
]
