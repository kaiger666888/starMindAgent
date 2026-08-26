"""学习记忆路由：历史会话 + 学习画像 + 推荐。

  GET  /memory/users/{user_id}/sessions           列出用户会话
  GET  /memory/sessions/{session_id}               会话详情(完整 QA 步骤树)
  GET  /memory/sessions/{session_id}/export       结构化导出(md 手账/json 备份)
  GET  /memory/users/{user_id}/profile             读学习画像(stale 标志)
  POST /memory/users/{user_id}/profile/refresh     触发 LLM 重新总结画像
"""
from __future__ import annotations
from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import Response

from app.memory import service
from app.memory.export import export_session
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


@router.get("/sessions/{session_id}/export")
async def session_export(
    session_id: str,
    format: str = Query("md", pattern="^(md|json)$"),
):
    """结构化导出一次学习：md=学习手账（可再导入开新探索），json=机器可读备份。"""
    out = await export_session(session_id, fmt=format)
    if out is None:
        raise HTTPException(status_code=404, detail=f"session {session_id} not found")
    # filename* 用 RFC 5987 百分号编码（中文标题 latin-1 头放不下）
    from urllib.parse import quote
    quoted = quote(out["filename"])
    return Response(
        content=out["content"],
        media_type=out["mime"] + "; charset=utf-8",
        headers={
            "Content-Disposition": f"attachment; filename*=UTF-8''{quoted}",
        },
    )


@router.get("/users/{user_id}/profile", response_model=ProfileResponse)
async def get_profile(user_id: str):
    out = await service.get_profile(user_id)
    if out is None:
        raise HTTPException(status_code=404, detail=f"profile for {user_id} not found, refresh first")
    return out


@router.post("/users/{user_id}/profile/refresh")
async def refresh_profile(user_id: str, force: bool = Query(False)):
    return await service.refresh_profile(user_id, force=force)
