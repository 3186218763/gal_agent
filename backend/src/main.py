"""
FastAPI 后端服务器
使用 OpenAI Agents SDK
"""
from fastapi import FastAPI, WebSocket, WebSocketDisconnect, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from typing import Optional
import uuid
import os

from .core import GameLoop, StateManager, ScriptParser
from .models import GameState

# 创建 FastAPI 应用
app = FastAPI(title="Galgame AI Backend")

# 配置 CORS
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:3000", "http://localhost:5173"],  # 前端地址
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# 全局实例
state_manager = StateManager(data_dir="data")
script_parser = ScriptParser(scripts_dir="scripts")


# API Models
class CreateSessionRequest(BaseModel):
    chapter_id: str = "chapter_01"


class CreateSessionResponse(BaseModel):
    session_id: str
    chapter_id: str


class SessionInfo(BaseModel):
    session_id: str
    current_chapter: str
    current_beat_index: int
    tension_level: int


# REST API Routes

@app.get("/")
async def root():
    """健康检查"""
    return {"status": "ok", "service": "Galgame AI Backend"}


@app.post("/api/sessions", response_model=CreateSessionResponse)
async def create_session(request: CreateSessionRequest):
    """创建新游戏会话"""
    session_id = str(uuid.uuid4())

    # 解析章节以获取初始角色关系
    try:
        metadata, _ = script_parser.parse_chapter(request.chapter_id)
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail=f"Chapter not found: {request.chapter_id}")

    # 构建角色初始关系
    characters = {}
    for char in metadata.characters:
        characters[char.id] = {
            'trust': char.initial_trust,
            'romance': char.initial_romance
        }

    # 创建会话
    game_state = state_manager.create_session(
        session_id=session_id,
        chapter_id=request.chapter_id,
        characters=characters
    )

    return CreateSessionResponse(
        session_id=session_id,
        chapter_id=request.chapter_id
    )


@app.get("/api/sessions/{session_id}", response_model=SessionInfo)
async def get_session(session_id: str):
    """获取会话信息"""
    game_state = state_manager.get_session(session_id)

    if not game_state:
        raise HTTPException(status_code=404, detail="Session not found")

    return SessionInfo(
        session_id=game_state.session_id,
        current_chapter=game_state.current_chapter,
        current_beat_index=game_state.current_beat_index,
        tension_level=game_state.tension_level
    )


@app.delete("/api/sessions/{session_id}")
async def delete_session(session_id: str):
    """删除会话"""
    success = state_manager.delete_session(session_id)

    if not success:
        raise HTTPException(status_code=404, detail="Session not found")

    return {"status": "deleted", "session_id": session_id}


@app.get("/api/sessions")
async def list_sessions():
    """列出所有会话"""
    sessions = state_manager.list_sessions()
    return {"sessions": sessions}


# WebSocket 端点

@app.websocket("/ws/game/{session_id}")
async def websocket_game(websocket: WebSocket, session_id: str):
    """游戏 WebSocket 连接"""
    await websocket.accept()

    try:
        # 检查会话是否存在
        game_state = state_manager.get_session(session_id)
        if not game_state:
            await websocket.send_json({
                "type": "error",
                "message": f"Session not found: {session_id}"
            })
            await websocket.close()
            return

        # 创建游戏循环
        game_loop = GameLoop(
            state_manager=state_manager,
            script_parser=script_parser
        )

        # 运行游戏
        await game_loop.run(session_id, websocket)

    except WebSocketDisconnect:
        print(f"WebSocket disconnected for session {session_id}")
    except Exception as e:
        print(f"Error in WebSocket handler: {e}")
        try:
            await websocket.send_json({
                "type": "error",
                "message": str(e)
            })
        except:
            pass


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
