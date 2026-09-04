"""记忆卡片复习路由：盲 check 复习 + 学习进度管理。

  GET    /memory/cards/users/{user_id}/due           今日到期复习队列
  GET    /memory/cards/users/{user_id}/progress      学习进度统计
  GET    /memory/cards/users/{user_id}/all            全部卡片（复习页外管理用）
  POST   /memory/cards/users/{user_id}/from-selection  阅读区选段主动建卡
  POST   /memory/cards/{card_id}/grade               盲 check 评分
  GET    /memory/cards/{card_id}                      卡片详情
  DELETE /memory/cards/{card_id}                      删卡

评分语义（grade 端点）：
- understood：streak+1；streak 达 3 → 归档（status=archived），due 不再排
- forgot：streak 清零，明天再到期
- retry（"明天再试"）：不重置 review_count 语义，streak 清零，明天再到期
"""
from __future__ import annotations
import logging
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, Field

from app.db import session_scope
from app.models.tables import MemoryCard, AppUser
from app.memory import cards as card_service

log = logging.getLogger(__name__)

router = APIRouter(prefix="/memory", tags=["memory-cards"])

_STREAK_TO_ARCHIVE = 3


def _card_out(c: MemoryCard) -> dict:
    return {
        "card_id": str(c.card_id),
        "user_id": c.user_id,
        "concept_id": str(c.concept_id) if c.concept_id else None,
        "qa_id": str(c.qa_id) if c.qa_id else None,
        "concept_name": c.concept_name,
        "question": c.question,
        "answer": c.answer,
        "source_answer": c.source_answer,
        "status": c.status,
        "streak": c.streak,
        "review_count": c.review_count,
        "due_at": c.due_at.isoformat() if c.due_at else None,
        "last_reviewed_at": c.last_reviewed_at.isoformat() if c.last_reviewed_at else None,
        "last_grade": c.last_grade,
        "generator_model": c.generator_model,
        "created_at": c.created_at.isoformat() if c.created_at else None,
    }


@router.get("/cards/users/{user_id}/due")
async def due_cards(user_id: str, limit: int = Query(50, ge=1, le=200)):
    """今日到期复习队列：active 且 due_at <= now，按 due_at 升序（最该复习的在前）。"""
    from sqlalchemy import select
    now = datetime.now(timezone.utc)
    async with session_scope() as s:
        rows = (
            await s.execute(
                select(MemoryCard)
                .where(MemoryCard.user_id == user_id)
                .where(MemoryCard.status == "active")
                .where(MemoryCard.due_at <= now)
                .order_by(MemoryCard.due_at.asc())
                .limit(limit)
            )
        ).scalars().all()
        return [_card_out(c) for c in rows]


@router.get("/cards/users/{user_id}/progress")
async def review_progress(user_id: str):
    """学习进度统计：复习页顶部进度区数据源。"""
    from sqlalchemy import select, func
    now = datetime.now(timezone.utc)
    async with session_scope() as s:
        rows = (
            await s.execute(
                select(MemoryCard.status, MemoryCard.streak, MemoryCard.due_at)
                .where(MemoryCard.user_id == user_id)
            )
        ).all()
    total = len(rows)
    archived = sum(1 for r in rows if r.status == "archived")
    active = [r for r in rows if r.status == "active"]
    due_now = sum(1 for r in active if r.due_at and r.due_at <= now)
    # 在途（active 但未到期）= 正在 3 天连续理解路上的卡
    in_flight = len(active) - due_now
    # 今日已复习：active 卡里 last_grade 不为空且 last_reviewed_at 在今天
    reviewed_today = 0
    today = now.date()
    # streak 分布（0/1/2 = 距归档还差几步）
    streak_dist = {"0": 0, "1": 0, "2": 0}
    for r in active:
        if r.streak in (0, 1, 2):
            streak_dist[str(r.streak)] += 1
    return {
        "user_id": user_id,
        "total": total,
        "archived": archived,
        "active": len(active),
        "due_now": due_now,
        "in_flight": in_flight,
        "streak_dist": streak_dist,
        "streak_to_archive": _STREAK_TO_ARCHIVE,
    }


@router.get("/cards/users/{user_id}/all")
async def all_cards(user_id: str, limit: int = Query(200, ge=1, le=1000)):
    """全部卡片（含归档），按创建时间倒序。复习页的"卡片库"区数据源。"""
    from sqlalchemy import select
    async with session_scope() as s:
        rows = (
            await s.execute(
                select(MemoryCard)
                .where(MemoryCard.user_id == user_id)
                .order_by(MemoryCard.created_at.desc())
                .limit(limit)
            )
        ).scalars().all()
        return [_card_out(c) for c in rows]


class SelectionCardRequest(BaseModel):
    """阅读区选中文字主动建卡。"""
    selected_text: str = Field(..., min_length=2, max_length=2000)
    qa_id: str | None = None          # 所在层（拿整层 QA 作背景）
    concept_id: str | None = None     # 归属概念（可选）
    question: str | None = None       # 所在层问题（前端直传可省一次查询）


@router.post("/cards/users/{user_id}/from-selection")
async def card_from_selection(user_id: str, req: SelectionCardRequest):
    """选中正文 → 生成记忆卡片（主动总结入口，同步返回卡片）。"""
    # upsert app_user（外键依赖）
    from sqlalchemy.dialects.postgresql import insert as pg_insert
    now = datetime.now(timezone.utc)
    async with session_scope() as s:
        stmt = pg_insert(AppUser).values(user_id=user_id, last_active_at=now).on_conflict_do_update(
            index_elements=[AppUser.user_id], set_={"last_active_at": now}
        )
        await s.execute(stmt)
    out = await card_service.generate_from_selection(
        user_id=user_id,
        selected_text=req.selected_text,
        qa_id=req.qa_id,
        concept_id=req.concept_id,
        question=req.question,
    )
    if not out.get("created"):
        raise HTTPException(status_code=409, detail="该选段已有相同卡片")
    return out


class GradeRequest(BaseModel):
    """盲 check 自评。grade: understood / forgot / retry。"""
    grade: str = Field(..., pattern="^(understood|forgot|retry)$")


@router.post("/cards/{card_id}/grade")
async def grade_card(card_id: str, req: GradeRequest):
    """盲 check 评分：
    - understood：streak+1，达到 3 归档；due=明天
    - forgot / retry：streak 归零，due=明天
    """
    from sqlalchemy import select
    tomorrow = datetime.now(timezone.utc) + timedelta(days=1)
    async with session_scope() as s:
        card = (
            await s.execute(
                select(MemoryCard).where(MemoryCard.card_id == card_id)
            )
        ).scalar_one_or_none()
        if card is None:
            raise HTTPException(status_code=404, detail=f"card {card_id} not found")
        if card.status == "archived":
            raise HTTPException(status_code=409, detail="card archived, no more review needed")
        was_streak = card.streak
        card.review_count += 1
        card.last_grade = req.grade
        card.last_reviewed_at = datetime.now(timezone.utc)
        card.due_at = tomorrow
        if req.grade == "understood":
            card.streak += 1
            if card.streak >= _STREAK_TO_ARCHIVE:
                card.status = "archived"
        else:
            card.streak = 0
        out = _card_out(card)
        out["just_archived"] = (was_streak + 1 >= _STREAK_TO_ARCHIVE) if req.grade == "understood" else False
        return out


@router.get("/cards/{card_id}")
async def card_detail(card_id: str):
    from sqlalchemy import select
    async with session_scope() as s:
        card = (
            await s.execute(
                select(MemoryCard).where(MemoryCard.card_id == card_id)
            )
        ).scalar_one_or_none()
        if card is None:
            raise HTTPException(status_code=404, detail=f"card {card_id} not found")
        return _card_out(card)


@router.delete("/cards/{card_id}")
async def delete_card(card_id: str):
    """删除卡片（复习页卡片库管理）。"""
    from sqlalchemy import select, delete
    async with session_scope() as s:
        res = await s.execute(
            delete(MemoryCard).where(MemoryCard.card_id == card_id)
        )
        if res.rowcount == 0:
            raise HTTPException(status_code=404, detail=f"card {card_id} not found")
        return {"deleted": card_id}
