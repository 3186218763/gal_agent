"""V2-only FastAPI contract for story sessions."""

from __future__ import annotations

import json
import logging
import os
from dataclasses import dataclass
from pathlib import Path
from uuid import uuid4

from fastapi import FastAPI, HTTPException
from fastapi.responses import JSONResponse, StreamingResponse
from openai import OpenAIError
from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)

from src.story.projection import (
    PackProjection,
    SessionProjection,
    project_pack,
    project_session,
)
from src.story.runtime.config import OpenCodeGoSettings
from src.story.runtime.contracts import (
    ActionResult,
    DecisionRequired,
    InvalidChoice,
    PackMismatch,
    RuntimeGenerationUnavailable,
    RuntimeRevisionConflict,
    RuntimeSessionEnded,
)
from src.story.runtime.model import build_model_bundle
from src.story.runtime.planner import SdkPlanner
from src.story.runtime.segment_contracts import (
    DirectorPort,
    GuardPort,
    SegmentWriterPort,
)
from src.story.runtime.service import RuntimeService
from src.story.runtime.turn_orchestrator import TurnOrchestrator
from src.story.runtime.writer import SdkWriter
from src.story.script_pack import PackCompileError, compile_script_pack
from src.story.script_pack.models import CompiledScriptPack
from src.story.state import initial_session_state
from src.story.storage import RevisionConflict, SessionNotFound, StoryEventStore


class PackNotFound(LookupError):
    """Raised when a script pack directory is missing."""


class ScriptPackRegistry:
    def __init__(self, root: Path) -> None:
        self.root = Path(root)
        self._cache: dict[str, CompiledScriptPack] = {}

    def get(self, pack_id: str) -> CompiledScriptPack:
        if pack_id not in self._cache:
            pack_path = self.root / pack_id
            if not pack_path.is_dir():
                raise PackNotFound(pack_id)
            pack = compile_script_pack(pack_path)
            if pack.source.identity.id != pack_id:
                raise PackCompileError("pack directory id does not match compiled pack id")
            self._cache[pack_id] = pack
        return self._cache[pack_id]


@dataclass(frozen=True)
class AppDependencies:
    store: StoryEventStore
    registry: ScriptPackRegistry
    runtime: RuntimeService | None = None
    orchestrator: TurnOrchestrator | None = None
    director: DirectorPort | None = None
    segment_writer: SegmentWriterPort | None = None
    guard: GuardPort | None = None


def default_dependencies() -> AppDependencies:
    settings = OpenCodeGoSettings.from_env()
    bundle = build_model_bundle(settings)
    store = StoryEventStore(Path(os.getenv("GAL_DATABASE_PATH", "data/story-v2.db")))
    registry = ScriptPackRegistry(Path(os.getenv("GAL_SCRIPT_PACK_ROOT", "script_packs")))
    from src.story.runtime.stream_writer import StreamingSceneGenerator

    runtime = RuntimeService(
        store,
        SdkPlanner(bundle.model),
        SdkWriter(bundle.model),
        StreamingSceneGenerator(bundle.client, settings.model),
    )
    return AppDependencies(store=store, registry=registry, runtime=runtime)


class CreateSessionRequest(BaseModel):
    pack_id: str
    session_seed: int


class RevisionRequest(BaseModel):
    expected_revision: int = Field(ge=0)
    idempotency_key: str = Field(min_length=1, max_length=120)


class ChoiceRequest(RevisionRequest):
    pass


class TurnRequest(BaseModel):
    expected_revision: int = Field(ge=0)
    idempotency_key: str = Field(min_length=1, max_length=120)
    choice_id: str | None = None


def _sse_error(code: str) -> str:
    return f"event: error\ndata: {json.dumps({'code': code})}\n\n"


def create_app(dependencies: AppDependencies | None = None) -> FastAPI:
    deps = dependencies or default_dependencies()
    app = FastAPI(title="Galgame AI V2")

    @app.exception_handler(PackNotFound)
    async def pack_not_found(request, exc):
        return JSONResponse(status_code=404, content={"detail": {"code": "pack_not_found"}})

    @app.exception_handler(SessionNotFound)
    async def session_not_found(request, exc):
        return JSONResponse(status_code=404, content={"detail": {"code": "session_not_found"}})

    @app.exception_handler(InvalidChoice)
    async def invalid_choice(request, exc):
        return JSONResponse(status_code=422, content={"detail": {"code": "invalid_choice"}})

    @app.exception_handler(DecisionRequired)
    @app.exception_handler(RuntimeRevisionConflict)
    @app.exception_handler(RevisionConflict)
    @app.exception_handler(PackMismatch)
    @app.exception_handler(RuntimeSessionEnded)
    async def command_conflict(request, exc):
        return JSONResponse(status_code=409, content={"detail": {"code": "command_conflict"}})

    @app.exception_handler(OpenAIError)
    async def provider_unavailable(request, exc):
        return JSONResponse(
            status_code=503,
            content={"detail": {"code": "model_provider_unavailable"}},
        )

    @app.exception_handler(RuntimeGenerationUnavailable)
    async def generation_unavailable(request, exc):
        return JSONResponse(
            status_code=503,
            content={"detail": {"code": "generation_unavailable"}},
        )

    @app.get("/health")
    async def health() -> dict[str, str]:
        return {"status": "ok", "runtime": "v2"}

    @app.post("/api/v2/sessions", response_model=SessionProjection, status_code=201)
    async def create_session(command: CreateSessionRequest) -> SessionProjection:
        try:
            pack = deps.registry.get(command.pack_id)
        except PackCompileError as exc:
            raise HTTPException(
                status_code=422,
                detail={"code": "invalid_script_pack"},
            ) from exc
        state = initial_session_state(pack, str(uuid4()), command.session_seed)
        deps.store.create_session(state)
        return project_session(state)

    @app.get("/api/v2/sessions/{session_id}", response_model=SessionProjection)
    async def get_session(session_id: str) -> SessionProjection:
        return project_session(deps.store.load_session(session_id))

    @app.get("/api/v2/packs/{pack_id}", response_model=PackProjection)
    async def get_pack(pack_id: str) -> PackProjection:
        return project_pack(deps.registry.get(pack_id))

    @app.post(
        "/api/v2/sessions/{session_id}/advance",
        response_class=StreamingResponse,
    )
    async def advance(session_id: str, command: RevisionRequest):
        if deps.runtime is None:
            raise HTTPException(
                status_code=503,
                detail={"code": "runtime_not_configured"},
            )
        state = deps.store.load_session(session_id)
        pack = deps.registry.get(state.pack_id)

        async def event_stream():
            try:
                async for event_type, data in deps.runtime.advance_streamed(
                    pack,
                    session_id,
                    command.expected_revision,
                    command.idempotency_key,
                ):
                    yield f"event: {event_type}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"
            except (RuntimeRevisionConflict, RevisionConflict):
                yield _sse_error("revision_conflict")
            except DecisionRequired:
                yield _sse_error("decision_required")
            except RuntimeSessionEnded:
                yield _sse_error("session_ended")
            except PackMismatch:
                yield _sse_error("pack_mismatch")
            except (OpenAIError, RuntimeGenerationUnavailable) as exc:
                logger.warning("advance stream failed: %s", exc)
                yield _sse_error("generation_unavailable")

        return StreamingResponse(
            event_stream(),
            media_type="text/event-stream",
            headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
        )

    @app.post(
        "/api/v2/sessions/{session_id}/choices/{choice_id}",
        response_model=ActionResult,
    )
    async def choose(
        session_id: str,
        choice_id: str,
        command: ChoiceRequest,
    ) -> ActionResult:
        if deps.runtime is None:
            raise HTTPException(
                status_code=503,
                detail={"code": "runtime_not_configured"},
            )
        state = deps.store.load_session(session_id)
        pack = deps.registry.get(state.pack_id)
        return await deps.runtime.select_choice(
            pack,
            session_id,
            choice_id,
            command.expected_revision,
            command.idempotency_key,
        )

    @app.post(
        "/api/v2/sessions/{session_id}/turns",
        response_class=StreamingResponse,
    )
    async def execute_turn(session_id: str, command: TurnRequest):
        if deps.orchestrator is None:
            raise HTTPException(
                status_code=503,
                detail={"code": "turn_orchestrator_not_configured"},
            )
        pack = deps.registry.get(deps.store.load_session(session_id).pack_id)

        async def event_stream():
            try:
                async for event_type, data in deps.orchestrator.execute_turn(
                    pack,
                    session_id,
                    command.expected_revision,
                    command.idempotency_key,
                    command.choice_id,
                ):
                    yield f"event: {event_type}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"
            except (RuntimeRevisionConflict, RevisionConflict):
                yield _sse_error("revision_conflict")
            except DecisionRequired:
                yield _sse_error("decision_required")
            except RuntimeSessionEnded:
                yield _sse_error("session_ended")
            except PackMismatch:
                yield _sse_error("pack_mismatch")
            except (OpenAIError, RuntimeGenerationUnavailable) as exc:
                logger.warning("turn stream failed: %s", exc)
                yield _sse_error("generation_unavailable")

        return StreamingResponse(
            event_stream(),
            media_type="text/event-stream",
            headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
        )

    return app


__all__ = [
    "AppDependencies",
    "ChoiceRequest",
    "CreateSessionRequest",
    "PackNotFound",
    "RevisionRequest",
    "ScriptPackRegistry",
    "TurnRequest",
    "create_app",
    "default_dependencies",
]
