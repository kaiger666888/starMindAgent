"""学习材料导入路由：

  POST /learning/import         导入 markdown 文件内容（json body）
  GET  /learning/materials       列出用户导入的材料
  GET  /learning/materials/{id}  获取材料详情（含 qa_id）
"""
from __future__ import annotations
from fastapi import APIRouter, HTTPException, Query, File, UploadFile
from pydantic import BaseModel
from typing import Optional

from app.learning import service

router = APIRouter(prefix="/learning", tags=["learning"])


class ImportRequest(BaseModel):
    user_id: str
    title: str
    content: str  # markdown 全文


@router.post("/import")
async def import_md(req: ImportRequest):
    """导入 markdown：存材料 + 建 L0 根 QAStep + 抽取概念。"""
    if not req.content.strip():
        raise HTTPException(status_code=400, detail="content is empty")
    return await service.import_markdown(req.user_id, req.title, req.content)


@router.post("/upload")
async def upload_md(user_id: str = Query(...), file: UploadFile = File(...)):
    """上传 .md 文件导入。"""
    if not file.filename or not file.filename.endswith((".md", ".markdown", ".txt")):
        raise HTTPException(status_code=400, detail="only .md/.markdown/.txt supported")
    content = (await file.read()).decode("utf-8", errors="ignore")
    title = file.filename.rsplit(".", 1)[0]
    return await service.import_markdown(user_id, title, content)


@router.get("/materials")
async def list_materials(user_id: str = Query(...)):
    from app.db import session_scope
    from sqlalchemy import select
    from app.models.tables import LearningMaterial
    async with session_scope() as s:
        rows = (
            await s.execute(
                select(LearningMaterial.material_id, LearningMaterial.title,
                       LearningMaterial.size_bytes, LearningMaterial.created_at)
                .where(LearningMaterial.user_id == user_id)
                .order_by(LearningMaterial.created_at.desc())
            )
        ).all()
    return [{
        "material_id": str(r[0]), "title": r[1],
        "size_bytes": r[2],
        "created_at": r[3].isoformat() if r[3] else None,
    } for r in rows]


@router.get("/materials/{material_id}")
async def get_material(material_id: str):
    from app.db import session_scope
    from sqlalchemy import select
    from app.models.tables import LearningMaterial
    async with session_scope() as s:
        mat = (
            await s.execute(select(LearningMaterial).where(LearningMaterial.material_id == material_id))
        ).scalar_one_or_none()
        if mat is None:
            raise HTTPException(status_code=404, detail="material not found")
        # 找关联的 L0 qa_id
        from app.models.tables import QAStep
        qa = (
            await s.execute(select(QAStep.qa_id).where(QAStep.material_id == mat.material_id).limit(1))
        ).scalar_one_or_none()
    return {
        "material_id": str(mat.material_id),
        "title": mat.title,
        "content": mat.content,
        "content_plain": mat.content_plain,
        "qa_id": str(qa) if qa else None,
    }
