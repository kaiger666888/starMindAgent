"""Concept 服务路由（P0 接口）。

  POST /concept/merge         merge_concepts(id_a, id_b)
  POST /concept/undo          undo_merge(merge_id)
  POST /concept/graph         get_graph(session_id, origin_filter)
  POST /concept/explore       increment_explore(concept_id)
"""
from __future__ import annotations
from fastapi import APIRouter, HTTPException

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
