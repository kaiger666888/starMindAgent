"""Concept 服务路由（P0 接口）。

  POST /concept/merge         merge_concepts(id_a, id_b)
  POST /concept/undo          undo_merge(merge_id)
  POST /concept/graph         get_graph(session_id, origin_filter)
  POST /concept/explore       increment_explore(concept_id, kind)
  GET  /concept/global        get_global_graph(user_id, origin_filter)  跨session聚合
  POST /concept/extend        extend_domain_graph(session_id, hops)      状态三动态扩展
  GET  /concept/{id}/history  get_concept_history(concept_id)            差异化引导
  POST /concept/correct       correct_annotation(qa_id, concept_id, action)  手动纠标注
"""
from __future__ import annotations
from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel
from typing import Optional

from app.concept import concept_service
from app.schemas import MergeRequest, UndoMergeRequest, GraphRequest, ExploreRequest

router = APIRouter(prefix="/concept", tags=["concept"])


@router.post("/merge")
async def merge(req: MergeRequest):
    try:
        return await concept_service.merge_concepts(req.id_a, req.id_b)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.post("/undo")
async def undo(req: UndoMergeRequest):
    try:
        return await concept_service.undo_merge(req.merge_id)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))


@router.post("/graph")
async def graph(req: GraphRequest):
    return await concept_service.get_graph(req.session_id, req.origin_filter)


@router.post("/explore")
async def explore(req: ExploreRequest):
    # 前端"标为概念下钻"用 local_* 临时 id 调本接口（本地选中文本还没入库成概念），
    # 非 UUID 直接进 UPDATE ... WHERE concept_id=$1::UUID 会炸 asyncpg -> 500。
    # 视为"尚未沉淀的概念"，跳过计数。
    if not _is_uuid(req.concept_id):
        return {"concept_id": req.concept_id, "explore_count": 0,
                "drill_down_count": 0, "visit_count": 0, "skipped": True}
    return await concept_service.increment_explore(req.concept_id)


def _is_uuid(v) -> bool:
    if not v or not isinstance(v, str) or len(v) != 36:
        return False
    try:
        import uuid as _uuid
        _uuid.UUID(v)
        return True
    except (ValueError, AttributeError):
        return False


@router.patch("/{concept_id}/understood")
async def set_understood(concept_id: str, understood: bool = Query(...)):
    """手动勾选/取消概念已理解（左边栏概念汇总 check 框）。"""
    if not _is_uuid(concept_id):
        raise HTTPException(status_code=400, detail="invalid concept_id")
    from app.db import session_scope
    from sqlalchemy import update as sa_update
    from app.models.tables import ConceptNode
    async with session_scope() as s:
        res = await s.execute(
            sa_update(ConceptNode)
            .where(ConceptNode.concept_id == concept_id)
            .values(understood=understood)
        )
        if res.rowcount == 0:
            raise HTTPException(status_code=404, detail=f"concept {concept_id} not found")
    return {"concept_id": concept_id, "understood": understood}


@router.get("/global")
async def global_graph(user_id: Optional[str] = Query(None)):
    return await concept_service.get_global_graph(user_id=user_id)


@router.post("/extend")
async def extend(session_id: str, hops: int = Query(1, ge=1, le=2)):
    return await concept_service.extend_domain_graph(session_id, hops=hops)


@router.get("/{concept_id}/history")
async def history(concept_id: str):
    return await concept_service.get_concept_history(concept_id)


class CorrectAnnotationRequest(BaseModel):
    qa_id: str
    concept_id: str
    action: str  # "add" | "remove"：补抽漏抽 / 删误抽


@router.post("/correct")
async def correct(req: CorrectAnnotationRequest):
    """手动纠标注：补漏抽概念 / 删误抽概念（需求五"手动纠标注入口"）。"""
    from app.db import session_scope
    from sqlalchemy import select, text
    from app.models.tables import QAStep
    if req.action not in ("add", "remove"):
        raise HTTPException(status_code=400, detail="action must be add|remove")
    async with session_scope() as s:
        row = (
            await s.execute(
                select(QAStep).where(QAStep.qa_id == req.qa_id)
            )
        ).scalar_one_or_none()
        if row is None:
            raise HTTPException(status_code=404, detail=f"qa_step {req.qa_id} not found")
        ids = list(row.extracted_concept_ids or [])
        cid = str(req.concept_id)
        if req.action == "add" and cid not in ids:
            ids.append(cid)
        elif req.action == "remove" and cid in ids:
            ids.remove(cid)
        from app.db import is_sqlite
        if is_sqlite():
            await s.execute(text(
                "UPDATE qa_step SET extracted_concept_ids = CAST(:ids AS json) "
                "WHERE qa_id = :id"
            ).bindparams(ids=__import__('json').dumps(ids), id=req.qa_id))
        else:
            await s.execute(text(
                "UPDATE qa_step SET extracted_concept_ids = :ids::jsonb "
                "WHERE qa_id = CAST(:id AS uuid)"
            ).bindparams(ids=__import__('json').dumps(ids), id=req.qa_id))
    return {"qa_id": req.qa_id, "concept_id": req.concept_id, "action": req.action, "extracted_concept_ids": ids}
