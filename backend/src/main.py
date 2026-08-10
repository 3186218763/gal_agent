"""
FastAPI 后端服务器 — WorldStore + GameKernel (stub agents for Task 10).
"""
from __future__ import annotations

import os
import uuid
from pathlib import Path
from typing import Optional

from fastapi import FastAPI, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from pydantic import AliasChoices, BaseModel, ConfigDict, Field

from src.content.setting_pack_loader import load_setting_pack
from src.core.world_store import WorldStore
from src.domain.events import EventDatabase
from src.domain.setting_pack import SettingPack
from src.domain.world_state import WorldState
from src.kernel.agent_factory import build_ports
from src.kernel.game_kernel import GameKernel

# Paths relative to backend/
BACKEND_DIR = Path(__file__).resolve().parents[1]
SCRIPTS_DIR = BACKEND_DIR / "scripts"
DATA_DIR = BACKEND_DIR / "data"

app = FastAPI(title="Galgame AI Backend")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:3000", "http://localhost:5173"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

world_store = WorldStore(DATA_DIR)


# ---------------------------------------------------------------------------
# API models
# ---------------------------------------------------------------------------


class CreateSessionRequest(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    pack_id: str = Field(
        default="chapter_01",
        validation_alias=AliasChoices("pack_id", "chapter_id"),
    )


class CreateSessionResponse(BaseModel):
    session_id: str
    pack_id: str
    # Frontend still types chapter_id; keep for compat.
    chapter_id: str


class SessionInfo(BaseModel):
    session_id: str
    pack_id: str
    steps: int
    tension: int
    phase: str


# ---------------------------------------------------------------------------
# Kernel factory (stubs only in Task 10)
# ---------------------------------------------------------------------------


def build_kernel(
    pack: SettingPack,
    state: WorldState,
    events: EventDatabase,
) -> GameKernel:
    """Build GameKernel with stub or SDK ports.

    Stubs when GAL_USE_STUBS != "0" (default) OR OPENAI_API_KEY is unset.
    Real SdkDirector only when GAL_USE_STUBS=0 and API key is present.
    """
    use_stubs = (
        os.environ.get("GAL_USE_STUBS", "1") != "0"
        or not os.environ.get("OPENAI_API_KEY")
    )
    director, character, choice, memory = build_ports(use_stubs=use_stubs)
    return GameKernel(
        pack,
        state,
        events,
        director,
        character,
        choice,
        memory,
    )


# ---------------------------------------------------------------------------
# REST
# ---------------------------------------------------------------------------


@app.get("/")
async def root():
    """健康检查"""
    return {"status": "ok", "service": "Galgame AI Backend"}


@app.post("/api/sessions", response_model=CreateSessionResponse)
async def create_session(request: CreateSessionRequest):
    """创建新游戏会话"""
    session_id = str(uuid.uuid4())
    try:
        pack = load_setting_pack(SCRIPTS_DIR, request.pack_id)
    except FileNotFoundError:
        raise HTTPException(
            status_code=404, detail=f"Pack not found: {request.pack_id}"
        )

    world_store.create_session(session_id, pack)
    return CreateSessionResponse(
        session_id=session_id,
        pack_id=request.pack_id,
        chapter_id=request.pack_id,
    )


@app.get("/api/sessions/{session_id}", response_model=SessionInfo)
async def get_session(session_id: str):
    """获取会话信息"""
    loaded = world_store.load(session_id)
    if not loaded:
        raise HTTPException(status_code=404, detail="Session not found")

    state, _events = loaded
    return SessionInfo(
        session_id=state.session_id,
        pack_id=state.pack_id,
        steps=state.steps,
        tension=state.tension,
        phase=state.phase.value if hasattr(state.phase, "value") else str(state.phase),
    )


@app.delete("/api/sessions/{session_id}")
async def delete_session(session_id: str):
    """删除会话"""
    success = world_store.delete(session_id)
    if not success:
        raise HTTPException(status_code=404, detail="Session not found")
    return {"status": "deleted", "session_id": session_id}


@app.get("/api/sessions")
async def list_sessions():
    """列出所有会话"""
    return {"sessions": world_store.list_sessions()}


# ---------------------------------------------------------------------------
# WebSocket
# ---------------------------------------------------------------------------


@app.websocket("/ws/game/{session_id}")
async def websocket_game(websocket: WebSocket, session_id: str):
    """游戏 WebSocket — GameKernel turn loop."""
    await websocket.accept()

    try:
        loaded = world_store.load(session_id)
        if not loaded:
            await websocket.send_json(
                {"type": "error", "message": f"Session not found: {session_id}"}
            )
            await websocket.close()
            return

        state, events = loaded
        try:
            pack = load_setting_pack(SCRIPTS_DIR, state.pack_id)
        except FileNotFoundError:
            await websocket.send_json(
                {
                    "type": "error",
                    "message": f"Pack not found: {state.pack_id}",
                }
            )
            await websocket.close()
            return

        kernel = build_kernel(pack, state, events)

        for msg in await kernel.start():
            await websocket.send_json(msg)
        # Persist tension fix / any start-side mutations
        world_store.save(session_id, kernel.state, kernel.events)

        while not kernel.state.ended:
            if kernel.state.pending_options:
                data = await websocket.receive_json()
                if data.get("type") != "player_choice":
                    await websocket.send_json(
                        {
                            "type": "error",
                            "message": "only player_choice allowed",
                        }
                    )
                    continue
                if "option_index" not in data:
                    await websocket.send_json(
                        {
                            "type": "error",
                            "message": "option_index required",
                        }
                    )
                    continue
                outs = await kernel.apply_player_choice(int(data["option_index"]))
            else:
                outs = await kernel.advance_reading()

            for m in outs:
                await websocket.send_json(m)

            world_store.save(session_id, kernel.state, kernel.events)

            if kernel.state.ended:
                break

    except WebSocketDisconnect:
        print(f"WebSocket disconnected for session {session_id}")
    except Exception as e:
        print(f"Error in WebSocket handler: {e}")
        try:
            await websocket.send_json({"type": "error", "message": str(e)})
        except Exception:
            pass


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=8000)
