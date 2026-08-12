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
    return await concept_service.increment_explore(req.concept_id)


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
        await s.execute(text(
            "UPDATE qa_step SET extracted_concept_ids = :ids::jsonb "
            "WHERE qa_id = CAST(:id AS uuid)"
        ).bindparams(ids=__import__('json').dumps(ids), id=req.qa_id))
    return {"qa_id": req.qa_id, "concept_id": req.concept_id, "action": req.action, "extracted_concept_ids": ids}
