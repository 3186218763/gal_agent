"""V2-only FastAPI contract for story sessions."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from uuid import uuid4

from fastapi import FastAPI, HTTPException
from fastapi.responses import JSONResponse
from openai import OpenAIError
from pydantic import BaseModel, Field

from src.story.runtime.config import OpenCodeGoSettings
from src.story.runtime.contracts import (
    ActionResult,
    DecisionRequired,
    InvalidChoice,
    PackMismatch,
    RuntimeGenerationUnavailable,
    RuntimeRevisionConflict,
    RuntimeScene,
    RuntimeSessionEnded,
)
from src.story.runtime.model import build_model_bundle
from src.story.runtime.planner import SdkPlanner
from src.story.runtime.service import RuntimeService
from src.story.runtime.writer import SdkWriter
from src.story.script_pack import PackCompileError, compile_script_pack
from src.story.script_pack.models import CompiledScriptPack
from src.story.state import NarrativeBlock, PresentedChoice, SessionState, initial_session_state
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
    runtime: RuntimeService


def default_dependencies() -> AppDependencies:
    settings = OpenCodeGoSettings.from_env()
    bundle = build_model_bundle(settings)
    store = StoryEventStore(Path(os.getenv("GAL_DATABASE_PATH", "data/story-v2.db")))
    registry = ScriptPackRegistry(Path(os.getenv("GAL_SCRIPT_PACK_ROOT", "script_packs")))
    runtime = RuntimeService(
        store,
        SdkPlanner(bundle.model),
        SdkWriter(bundle.model),
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


class SessionResponse(BaseModel):
    session_id: str
    pack_id: str
    revision: int
    status: str
    phase: str
    scene_count: int
    pending_decision_id: str | None
    scene_id: str | None
    blocks: tuple[NarrativeBlock, ...] = ()
    choices: tuple[PresentedChoice, ...] = ()
    ending_id: str | None = None
    ending_title: str | None = None

    @classmethod
    def from_state(cls, state: SessionState) -> SessionResponse:
        if state.pending_scene is not None:
            scene_id = state.pending_scene.scene_id
            blocks = state.pending_scene.blocks
        elif state.ending is not None:
            # SessionEnded clears pending_scene; epilogue lives on ending.
            scene_id = None
            blocks = state.ending.blocks
        else:
            scene_id = None
            blocks = ()
        return cls(
            session_id=state.session_id,
            pack_id=state.pack_id,
            revision=state.revision,
            status=state.status.value,
            phase=state.world.phase.value,
            scene_count=state.world.scene_count,
            pending_decision_id=(
                state.pending_decision.decision_id
                if state.pending_decision is not None
                else None
            ),
            scene_id=scene_id,
            blocks=blocks,
            choices=(
                state.pending_decision.choices if state.pending_decision is not None else ()
            ),
            ending_id=state.ending.ending_id if state.ending is not None else None,
            ending_title=state.ending.title if state.ending is not None else None,
        )


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

    @app.post("/api/v2/sessions", response_model=SessionResponse, status_code=201)
    async def create_session(command: CreateSessionRequest) -> SessionResponse:
        try:
            pack = deps.registry.get(command.pack_id)
        except PackCompileError as exc:
            raise HTTPException(
                status_code=422,
                detail={"code": "invalid_script_pack"},
            ) from exc
        state = initial_session_state(pack, str(uuid4()), command.session_seed)
        deps.store.create_session(state)
        return SessionResponse.from_state(state)

    @app.get("/api/v2/sessions/{session_id}", response_model=SessionResponse)
    async def get_session(session_id: str) -> SessionResponse:
        return SessionResponse.from_state(deps.store.load_session(session_id))

    @app.post("/api/v2/sessions/{session_id}/advance", response_model=RuntimeScene)
    async def advance(session_id: str, command: RevisionRequest) -> RuntimeScene:
        state = deps.store.load_session(session_id)
        pack = deps.registry.get(state.pack_id)
        return await deps.runtime.advance(
            pack,
            session_id,
            command.expected_revision,
            command.idempotency_key,
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
        state = deps.store.load_session(session_id)
        pack = deps.registry.get(state.pack_id)
        return await deps.runtime.select_choice(
            pack,
            session_id,
            choice_id,
            command.expected_revision,
            command.idempotency_key,
        )

    return app


__all__ = [
    "AppDependencies",
    "ChoiceRequest",
    "CreateSessionRequest",
    "PackNotFound",
    "RevisionRequest",
    "ScriptPackRegistry",
    "SessionResponse",
    "create_app",
    "default_dependencies",
]
