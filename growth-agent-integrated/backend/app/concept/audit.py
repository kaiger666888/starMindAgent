"""Audit log 反向回放，支持 undo_merge。

技术架构文档：所有合并写 audit log，支持基于 audit log 反向回放的撤销操作。
回放 = 取该 merge_id 的 audit_log.payload 快照，反向执行每一步。
"""
from __future__ import annotations
import logging
from sqlalchemy import select
from app.db import session_scope
from app.models.tables import ConceptNode, ConceptEdge, AuditLog

log = logging.getLogger(__name__)


async def replay_undo(merge_id: str) -> dict:
    """读取 merge_id 的 audit_log 快照，反向回放撤销合并。"""
    async with session_scope() as s:
        merge_log = (
            await s.execute(
                select(AuditLog).where(
                    AuditLog.merge_id == merge_id, AuditLog.action == "merge"
                ).order_by(AuditLog.log_id.desc()).limit(1)
            )
        ).scalar_one_or_none()
        if merge_log is None:
            raise ValueError(f"merge {merge_id} not found, cannot undo")

        snap = merge_log.payload or {}
        survivor_id = snap["survivor_id"]
        absorbed_id = snap["absorbed_id"]

        # 1) 还原 survivor 的 aliases 到合并前
        survivor = (
            await s.execute(select(ConceptNode).where(ConceptNode.concept_id == survivor_id))
        ).scalar_one()
        survivor.aliases = list(snap.get("survivor_aliases_before", []))
        survivor.explore_count -= int(snap.get("absorbed_explore_count", 0))

        # 2) 重建被吸收节点
        recreated = ConceptNode(
            concept_id=absorbed_id,
            canonical_name=snap["absorbed_name"],
            aliases=list(snap.get("absorbed_aliases", [])),
            source=snap.get("absorbed_source", "llm_extracted"),
            explore_count=int(snap.get("absorbed_explore_count", 0)),
        )
        s.add(recreated)

        # 3) 边反向回放：迁移过的边改回指向 absorbed
        for einfo in snap.get("edges_to_repoint", []):
            e = (
                await s.execute(select(ConceptEdge).where(ConceptEdge.edge_id == einfo["edge_id"]))
            ).scalar_one_or_none()
            if e is None:
                continue  # 边可能已删（去重时删的），忽略
            if einfo["was_source"]:
                e.source_id = absorbed_id
            else:
                e.target_id = absorbed_id

        # 4) 写 undo audit log（同通道，action=undo）
        s.add(AuditLog(
            qa_id=merge_log.qa_id, session_id=merge_log.session_id,
            action="undo", merge_id=merge_id,
            survivor_id=survivor_id, absorbed_id=absorbed_id,
            payload={"reverted_merge_log_id": merge_log.log_id},
        ))
        await s.flush()
        return {
            "merge_id": merge_id,
            "restored_concept_id": absorbed_id,
            "survivor_id": survivor_id,
        }
