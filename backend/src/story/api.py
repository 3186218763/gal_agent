"""V2-only FastAPI contract for story sessions."""

from __future__ import annotations

import asyncio
import json
import logging
import os
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
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
    DecisionRequired,
    InvalidChoice,
    PackMismatch,
    RuntimeGenerationUnavailable,
    RuntimeRevisionConflict,
    RuntimeSessionEnded,
)
from src.story.runtime.model import build_model_bundle
from src.story.runtime.pack_cache import PackCache
from src.story.runtime.planner import SdkPlanner
from src.story.runtime.segment_contracts import (
    DirectorPort,
    GuardPort,
    SegmentWriterPort,
)
from src.story.runtime.turn_orchestrator import TurnOrchestrator
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
    orchestrator: TurnOrchestrator | None = None
    director: DirectorPort | None = None
    segment_writer: SegmentWriterPort | None = None
    guard: GuardPort | None = None
    pack_cache: PackCache | None = None
    semantic_judge: object | None = None
    unified_agent_factory: object | None = None


def default_dependencies() -> AppDependencies:
    settings = OpenCodeGoSettings.from_env()
    bundle = build_model_bundle(settings)
    store = StoryEventStore(Path(os.getenv("GAL_DATABASE_PATH", "data/story-v2.db")))
    registry = ScriptPackRegistry(Path(os.getenv("GAL_SCRIPT_PACK_ROOT", "script_packs")))
    from src.story.runtime.completion_judge import CompletionJudge
    from src.story.runtime.director import SdkDirector
    from src.story.runtime.guard import Guard
    from src.story.runtime.pack_cache import PackCache
    from src.story.runtime.segment_writer import SdkSegmentWriter
    from src.story.runtime.semantic_judge import SdkSemanticJudge

    director = SdkDirector(bundle.model)
    segment_writer = SdkSegmentWriter(bundle.model)
    guard = Guard()
    from src.story.runtime.unified_segment import (
        OPENING_INSTRUCTIONS,
        SdkUnifiedSegmentAgent,
    )

    unified_agent = SdkUnifiedSegmentAgent(bundle.model)
    semantic_judge = SdkSemanticJudge(bundle.model)
    pack_cache = PackCache(Path(os.getenv("GAL_PACK_CACHE_ROOT", "data/pack_cache")))

    def unified_agent_factory():
        return SdkUnifiedSegmentAgent(bundle.model, instructions=OPENING_INSTRUCTIONS)

    orchestrator = TurnOrchestrator(
        store,
        director,
        segment_writer,
        guard,
        CompletionJudge(),
        planner=SdkPlanner(bundle.model),
        unified_agent=unified_agent,
        pack_cache=pack_cache,
        semantic_judge=semantic_judge,
    )
    return AppDependencies(
        store=store,
        registry=registry,
        orchestrator=orchestrator,
        director=director,
        segment_writer=segment_writer,
        guard=guard,
        pack_cache=pack_cache,
        semantic_judge=semantic_judge,
        unified_agent_factory=unified_agent_factory,
    )


class CreateSessionRequest(BaseModel):
    pack_id: str
    session_seed: int


class TurnRequest(BaseModel):
    expected_revision: int = Field(ge=0)
    idempotency_key: str = Field(min_length=1, max_length=120)
    choice_id: str | None = None


def _sse_error(code: str) -> str:
    return f"event: error\ndata: {json.dumps({'code': code})}\n\n"


def _discover_pack_dirs(root: Path) -> list[Path]:
    """Return every immediate sub-directory that contains a ``pack.yaml``."""
    if not root.is_dir():
        return []
    return [d for d in sorted(root.iterdir()) if d.is_dir() and (d / "pack.yaml").exists()]


async def _warmup_opening_caches(deps: AppDependencies) -> None:
    """Ensure every known pack has a cached Opening Segment.

    Called during API startup so the first player never waits for opening
    generation.  Failures are logged and skipped — the first player will
    get a live generation as fallback.
    """
    if deps.pack_cache is None or deps.unified_agent_factory is None:
        logger.info("opening cache warmup skipped (no pack_cache or model agent)")
        return

    for pack_dir in _discover_pack_dirs(deps.registry.root):
        try:
            pack_id = pack_dir.name
            pack = deps.registry.get(pack_id)
            if deps.pack_cache.has_opening(pack.pack_hash):
                logger.info("opening cache hit for pack=%s", pack_id)
                continue
            logger.info("warming opening cache for pack=%s ...", pack_id)
            from src.story.cli import ensure_opening_cache

            await ensure_opening_cache(
                pack=pack,
                cache=deps.pack_cache,
                opening_agent=deps.unified_agent_factory(),
                guard=deps.guard or _NoopGuard(),
            )
            logger.info("opening cache ready for pack=%s", pack_id)
        except Exception:
            logger.warning("opening cache warmup failed for pack=%s", pack_id, exc_info=True)


@asynccontextmanager
async def _app_lifespan(app: FastAPI) -> AsyncIterator[None]:
    """FastAPI lifespan: warm up opening caches in the background.

    The server starts serving immediately; the warmup task runs
    concurrently.  If the model is slow or unavailable, the first player
    gets a live generation as fallback — the server is never blocked.
    """
    deps: AppDependencies | None = getattr(app.state, "deps", None)
    task: asyncio.Task | None = None
    if deps is not None:
        task = asyncio.create_task(_warmup_opening_caches(deps))
    yield
    if task is not None and not task.done():
        task.cancel()


class _NoopGuard:
    """Fallback guard that always passes — used only when deps.guard is None."""

    def check_segment(self, *args, **kwargs):  # type: ignore[no-untyped-def]
        from src.story.runtime.segment_contracts import GuardResult

        return GuardResult(passed=True)


def create_app(dependencies: AppDependencies | None = None) -> FastAPI:
    deps = dependencies or default_dependencies()
    app = FastAPI(title="Galgame AI V2", lifespan=_app_lifespan)
    app.state.deps = deps

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
        deps.store.create_session(state, pack=pack)
        return project_session(state, pack=pack)

    @app.get("/api/v2/sessions/{session_id}", response_model=SessionProjection)
    async def get_session(session_id: str) -> SessionProjection:
        state = deps.store.load_session(session_id)
        pack = deps.store.load_pack_version(state.pack_hash)
        return project_session(state, pack=pack)

    @app.get("/api/v2/packs/{pack_id}", response_model=PackProjection)
    async def get_pack(pack_id: str) -> PackProjection:
        return project_pack(deps.registry.get(pack_id))

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
        state = deps.store.load_session(session_id)
        pack = deps.store.load_pack_version(state.pack_hash)

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
            except InvalidChoice:
                yield _sse_error("invalid_choice")
            except RuntimeSessionEnded:
                yield _sse_error("session_ended")
            except PackMismatch:
                yield _sse_error("pack_mismatch")
            except (OpenAIError, RuntimeGenerationUnavailable) as exc:
                logger.warning("turn stream failed: %s", exc)
                yield _sse_error("generation_unavailable")
            except Exception:
                logger.exception("turn stream unexpected error")
                yield _sse_error("internal_error")

        return StreamingResponse(
            event_stream(),
            media_type="text/event-stream",
            headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
        )

    return app


__all__ = [
    "AppDependencies",
    "CreateSessionRequest",
    "PackNotFound",
    "ScriptPackRegistry",
    "TurnRequest",
    "create_app",
    "default_dependencies",
]
