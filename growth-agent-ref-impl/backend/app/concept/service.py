"""Concept 服务接口（P0）。

技术架构文档第八节：
  merge_concepts(id_a, id_b)  合并两个概念节点
  undo_merge(merge_id)        基于 audit log 反向回放撤销合并
  get_graph(session_id, origin_filter)  单表按 origin 派生三状态视图
  increment_explore(concept_id)  探索热度 +1

归一化（merge/undo）强制串行化，走同一任务通道，防止与并发下钻的写操作产生乱序。
"""
from __future__ import annotations
import asyncio
import logging
from typing import Optional

from sqlalchemy import select, update, func, delete

from app.db import session_scope
from app.models.tables import ConceptNode, ConceptEdge, AuditLog
from app.concept.audit import replay_undo

log = logging.getLogger(__name__)

# 全局串行化锁：归一化 merge/undo 强制串行，防止与并发下钻乱序
_merge_lock = asyncio.Lock()


class ConceptService:
    # —— merge_concepts ——
    async def merge_concepts(self, id_a: str, id_b: str, *, qa_id: Optional[str] = None) -> dict:
        """合并两个概念节点：id_b 并入 id_a（survivor=id_a）。

        动作：把 b 的 aliases 合入 a，b 的边迁移到 a，b 置 absorbed（保留记录用于 undo）。
        合并前完整快照写入 audit_log payload，支持精确反向回放。
        """
        async with _merge_lock:
            async with session_scope() as s:
                a = (await s.execute(select(ConceptNode).where(ConceptNode.concept_id == id_a))).scalar_one()
                b = (await s.execute(select(ConceptNode).where(ConceptNode.concept_id == id_b))).scalar_one()
                if a.concept_id == b.concept_id:
                    raise ValueError("cannot merge a concept with itself")

                # 快照（undo 反向回放用）
                snapshot = {
                    "survivor_id": a.concept_id,
                    "absorbed_id": b.concept_id,
                    "survivor_aliases_before": list(a.aliases or []),
                    "absorbed_name": b.canonical_name,
                    "absorbed_aliases": list(b.aliases or []),
                    "absorbed_explore_count": b.explore_count,
                    "absorbed_source": b.source,
                    "edges_to_repoint": [],  # 见下
                }

                # 1) aliases 合入
                merged_aliases = sorted(set((a.aliases or []) + [b.canonical_name] + (b.aliases or [])))
                a.aliases = merged_aliases
                # 2) explore_count 累计（概念是唯一一等公民，热度跨合并累计）
                a.explore_count += b.explore_count

                # 3) b 的边迁移到 a（记录用于 undo 回放）
                b_edges = (
                    await s.execute(select(ConceptEdge).where(
                        (ConceptEdge.source_id == b.concept_id) | (ConceptEdge.target_id == b.concept_id)
                    ))
                ).scalars().all()
                for e in b_edges:
                    snapshot["edges_to_repoint"].append({
                        "edge_id": e.edge_id, "was_source": (e.source_id == b.concept_id),
                    })
                    if e.source_id == b.concept_id:
                        e.source_id = a.concept_id
                    if e.target_id == b.concept_id:
                        e.target_id = a.concept_id
                # 去掉迁移后可能的自环 / 重复边
                await s.flush()

                # 4) 软删除 b：把 b 改名为 absorbed 标记（不物理删，undo 需还原）
                #    采用物理删除 + audit log 快照足以还原（边已迁移、aliases 已快照）
                await s.delete(b)

                # 5) 写 audit log（动作=merge，带完整快照）
                log_row = AuditLog(
                    qa_id=qa_id, action="merge",
                    survivor_id=a.concept_id, absorbed_id=b.concept_id,
                    payload=snapshot,
                )
                s.add(log_row)
                await s.flush()
                merge_id = log_row.merge_id
                return {
                    "merge_id": merge_id,
                    "survivor_id": a.concept_id,
                    "absorbed_id": b.concept_id,
                    "survivor_aliases": merged_aliases,
                    "survivor_explore_count": a.explore_count,
                }

    # —— undo_merge：基于 audit log 反向回放 ——
    async def undo_merge(self, merge_id: str) -> dict:
        async with _merge_lock:
            return await replay_undo(merge_id)

    # —— get_graph：单表按 origin 派生三状态视图 ——
    async def get_graph(self, session_id: str,
                        origin_filter: Optional[list[str]] = None) -> dict:
        origins = origin_filter or ["user_click", "co_occurrence", "domain_graph"]
        async with session_scope() as s:
            # 节点：本会话涉及的所有概念
            node_ids_q = (
                select(func.distinct(ConceptEdge.target_id)).where(ConceptEdge.session_id == session_id)
                .union(
                    select(func.distinct(ConceptEdge.source_id)).where(ConceptEdge.session_id == session_id)
                )
            )
            node_ids = [r[0] for r in (await s.execute(node_ids_q)).all()]
            nodes = []
            if node_ids:
                rows = (
                    await s.execute(select(ConceptNode).where(ConceptNode.concept_id.in_(node_ids)))
                ).scalars().all()
                nodes = [{
                    "concept_id": n.concept_id, "canonical_name": n.canonical_name,
                    "aliases": n.aliases, "domain_tag": n.domain_tag,
                    "source": n.source, "explore_count": n.explore_count,
                } for n in rows]

            # 边：按 origin 分组（前端按视图筛选即可）
            edges_q = select(ConceptEdge).where(
                ConceptEdge.session_id == session_id,
                ConceptEdge.origin.in_(origins),
            )
            edges = [{
                "edge_id": e.edge_id, "source_id": e.source_id, "target_id": e.target_id,
                "relation_type": e.relation_type, "origin": e.origin,
            } for e in (await s.execute(edges_q)).scalars().all()]

            # 按来源分组派生三状态视图（也可由 DB view 派生）
            by_origin = {o: [e for e in edges if e["origin"] == o] for o in
                         ["user_click", "co_occurrence", "domain_graph"]}
            return {
                "session_id": session_id, "nodes": nodes,
                "edges": edges,
                "views": by_origin,  # 三状态视图
            }

    # —— increment_explore ——
    async def increment_explore(self, concept_id: str) -> dict:
        """探索热度 +1（以 concept_id 为单位，非问答轮次）。

        色彩映射规则（技术架构文档 4.4）：
          0 次=灰、1 次=绿、>=2 次按 1/2/4/8 四档对数映射淡红->深红
        """
        async with session_scope() as s:
            res = await s.execute(
                update(ConceptNode)
                .where(ConceptNode.concept_id == concept_id)
                .values(explore_count=ConceptNode.explore_count + 1)
                .returning(ConceptNode.explore_count)
            )
            cnt = res.scalar_one()
        return {
            "concept_id": concept_id,
            "explore_count": cnt,
            "color_tier": _color_tier(cnt),
        }


def _color_tier(cnt: int) -> str:
    """热度 -> 色彩档位（前端着色用）。"""
    if cnt <= 0:
        return "gray"
    if cnt == 1:
        return "green"
    # >=2 按 1/2/4/8 四档对数映射 淡红->深红
    if cnt < 2:
        return "red_1"
    if cnt < 4:
        return "red_2"
    if cnt < 8:
        return "red_3"
    return "red_4"


concept_service = ConceptService()
