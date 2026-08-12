"""学习记忆路由：历史会话 + 学习画像 + 推荐。

  GET  /memory/users/{user_id}/sessions           列出用户会话
  GET  /memory/sessions/{session_id}               会话详情(完整 QA 步骤树)
  GET  /memory/users/{user_id}/profile             读学习画像(stale 标志)
  POST /memory/users/{user_id}/profile/refresh     触发 LLM 重新总结画像
"""
from __future__ import annotations
from fastapi import APIRouter, HTTPException, Query

from app.memory import service
from app.schemas import SessionSummary, SessionDetail, ProfileResponse

router = APIRouter(prefix="/memory", tags=["memory"])


@router.get("/users/{user_id}/sessions", response_model=list[SessionSummary])
async def list_sessions(user_id: str, limit: int = Query(50, ge=1, le=200)):
    return await service.list_sessions(user_id, limit=limit)


@router.get("/sessions/{session_id}", response_model=SessionDetail)
async def session_detail(session_id: str):
    out = await service.get_session_detail(session_id)
    if out is None:
        raise HTTPException(status_code=404, detail=f"session {session_id} not found")
    return out


@router.get("/users/{user_id}/profile", response_model=ProfileResponse)
async def get_profile(user_id: str):
    out = await service.get_profile(user_id)
    if out is None:
        raise HTTPException(status_code=404, detail=f"profile for {user_id} not found, refresh first")
    return out


@router.post("/users/{user_id}/profile/refresh")
async def refresh_profile(user_id: str, force: bool = Query(False)):
    return await service.refresh_profile(user_id, force=force)
