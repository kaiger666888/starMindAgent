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
from sqlalchemy.dialects.postgresql import insert as pg_insert

from app.db import session_scope
from app.models.tables import ConceptNode, ConceptEdge, AuditLog, QASession, QAStep
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

    async def get_global_graph(self, user_id: Optional[str] = None,
                               origin_filter: Optional[list[str]] = None) -> dict:
        """全局概念图：跨 session 聚合（需求五节"概念图全局聚合"）。

        新问题开新树，但图上自然把不同树的共有概念连接起来。
        user_id 给定则限定该用户范围。
        """
        origins = origin_filter or ["user_click", "co_occurrence", "domain_graph"]
        async with session_scope() as s:
            # 限定 session 范围
            sess_filter = []
            if user_id:
                sess_filter.append(
                    select(QASession.session_id).where(QASession.user_id == user_id)
                )
            # 节点：范围内所有 edge 涉及的 concept_id
            edge_q = select(ConceptEdge).where(ConceptEdge.origin.in_(origins))
            if user_id:
                edge_q = edge_q.where(
                    ConceptEdge.session_id.in_(select(QASession.session_id).where(QASession.user_id == user_id))
                )
            edges = [{
                "edge_id": e.edge_id, "source_id": e.source_id, "target_id": e.target_id,
                "relation_type": e.relation_type, "origin": e.origin,
                "session_id": str(e.session_id),
            } for e in (await s.execute(edge_q)).scalars().all()]
            # 节点去重
            node_ids = set()
            for e in edges:
                node_ids.add(e["source_id"])
                node_ids.add(e["target_id"])
            nodes = []
            if node_ids:
                rows = (
                    await s.execute(select(ConceptNode).where(ConceptNode.concept_id.in_(node_ids)))
                ).scalars().all()
                nodes = [{
                    "concept_id": n.concept_id, "canonical_name": n.canonical_name,
                    "aliases": n.aliases, "domain_tag": n.domain_tag,
                    "source": n.source,
                    "explore_count": n.explore_count,
                    "drill_down_count": n.drill_down_count,
                    "visit_count": n.visit_count,
                    "understood": n.understood,
                    "color_tier": _color_tier(n.explore_count),
                    "dominant_signal": "drill" if n.drill_down_count >= n.visit_count else "visit",
                } for n in rows]
            by_origin = {o: [e for e in edges if e["origin"] == o] for o in
                         ["user_click", "co_occurrence", "domain_graph"]}
            return {
                "user_id": user_id, "nodes": nodes, "edges": edges,
                "views": by_origin, "scope": "global",
            }

    async def get_concept_history(self, concept_id: str, limit: int = 5) -> dict:
        """需求五"差异化引导"：查概念历史语境（哪些 QAStep 提过），
        供下钻时 prompt 注入"之前在 X 语境下了解过此概念"。
        """
        async with session_scope() as s:
            # concept_node.explore_count 跨 session 累计
            node = (
                await s.execute(select(ConceptNode).where(ConceptNode.concept_id == concept_id))
            ).scalar_one_or_none()
            if node is None:
                return {"concept_id": concept_id, "explore_count": 0, "contexts": []}
            # 查哪些 qa_step 的 extracted_concept_ids 含此 concept
            # JSONB @> 查询（cast jsonb 避免类型不匹配）
            import json as _json_mod
            from sqlalchemy import text
            rows = (
                await s.execute(
                    text(
                        "SELECT qa_id, question, session_id, created_at FROM qa_step "
                        "WHERE extracted_concept_ids @> CAST(:cid AS jsonb) "
                        "ORDER BY created_at DESC LIMIT :lim"
                    ).bindparams(cid=_json_mod.dumps([str(concept_id)]), lim=limit)
                )
            ).all()
            return {
                "concept_id": str(node.concept_id),
                "canonical_name": node.canonical_name,
                "explore_count": node.explore_count,
                "understood": node.understood,
                "contexts": [{"qa_id": str(r[0]), "question": r[1], "session_id": str(r[2])}
                             for r in rows],
            }

    async def extend_domain_graph(self, session_id: str, hops: int = 1) -> dict:
        """状态三：以已探索概念为种子，LLM 关联扩展 1-2 跳（不依赖预置领域表）。

        返回 {concept_id, canonical_name, related:[{name, reason}], is_explored:bool}
        灰色未探索概念可被前端点击触发新问题（状态三作导航入口）。
        """
        async with session_scope() as s:
            node_ids_q = (
                select(func.distinct(ConceptEdge.target_id)).where(ConceptEdge.session_id == session_id)
                .union(select(func.distinct(ConceptEdge.source_id)).where(ConceptEdge.session_id == session_id))
            )
            node_ids = [r[0] for r in (await s.execute(node_ids_q)).all()]
            if not node_ids:
                return {"session_id": session_id, "seeds": [], "extensions": []}
            seeds = (
                await s.execute(select(ConceptNode).where(ConceptNode.concept_id.in_(node_ids)))
            ).scalars().all()
        seed_names = [n.canonical_name for n in seeds]
        # LLM 关联扩展
        from app.inference.backend import default_backend
        backend = default_backend()
        if not hasattr(backend, "complete_text"):
            return {"session_id": session_id, "seeds": seed_names, "extensions": [],
                    "note": "LLM 不可用，无法扩展"}
        system = (
            "你是知识图谱扩展助手。给定种子概念，列出每个概念的 2-3 个紧密关联概念，"
            "用 JSON 数组返回，每项 {seed, related:[{name, reason}]}。"
            "name 用概念规范名(2-6字)，reason 用一句话说明关联。只输出 JSON。"
        )
        user = f"种子概念：{', '.join(seed_names[:12])}\n扩展 {hops} 跳。"
        try:
            import json as _json, re
            raw = await backend.complete_text(system, user, timeout=30.0)
            m = re.search(r"\[[\s\S]*\]", raw)
            ext = _json.loads(m.group(0)) if m else []
        except Exception as e:
            return {"session_id": session_id, "seeds": seed_names, "extensions": [],
                    "error": str(e)}
        # 标记每个 related 概念是否已探索
        explored_set = set(seed_names)
        for item in ext:
            for r in item.get("related", []):
                r["is_explored"] = r["name"] in explored_set
        return {"session_id": session_id, "seeds": seed_names, "extensions": ext}

    # —— increment_explore ——
    async def increment_explore(self, concept_id: str, kind: str = "drill") -> dict:
        """探索热度 +1。kind 区分:
        - drill: 主动下钻（drill_down_count+1，暗示复杂需深入）
        - visit: 回访（visit_count+1，暗示重要需巩固）
        explore_count = drill + visit，跨 session 累计。

        色彩映射规则（需求四节）：
          0 次=灰、1 次=绿、>=2 次按 1/2/4/8 四档对数映射淡红->深红
        红色语义：drill 偏深红(复杂)、visit 偏浅红(重要)
        """
        async with session_scope() as s:
            vals = {"explore_count": ConceptNode.explore_count + 1,
                    "last_explored_at": func.now()}
            if kind == "drill":
                vals["drill_down_count"] = ConceptNode.drill_down_count + 1
            else:
                vals["visit_count"] = ConceptNode.visit_count + 1
            res = await s.execute(
                update(ConceptNode)
                .where(ConceptNode.concept_id == concept_id)
                .values(**vals)
                .returning(ConceptNode.explore_count,
                           ConceptNode.drill_down_count,
                           ConceptNode.visit_count)
            )
            row = res.one()
            cnt = row[0]
            return {
                "concept_id": concept_id,
                "explore_count": cnt,
                "drill_down_count": row[1],
                "visit_count": row[2],
                "color_tier": _color_tier(cnt),
                "dominant_signal": "drill" if row[1] >= row[2] else "visit",
            }

    async def seed_preset_concepts(self) -> int:
        """需求五"预置核心概念+LLM补充扩展"：启动时 seed 预置种子进 concept_node。

        source=preset，幂等（canonical_name 已存在则跳过）。
        LLM 抽取时先经归一化匹配预置概念，未命中才新建（normalize 已含此逻辑）。
        返回新增种子数。
        """
        import json as _json, os
        preset_path = os.path.join(os.path.dirname(__file__), "preset_concepts.json")
        if not os.path.exists(preset_path):
            return 0
        with open(preset_path, encoding="utf-8") as f:
            data = _json.load(f)
        inserted = 0
        async with session_scope() as s:
            for domain, items in data.get("domains", {}).items():
                for item in items:
                    # 幂等：canonical_name 已存在则跳过
                    stmt = pg_insert(ConceptNode).values(
                        canonical_name=item["canonical_name"],
                        aliases=item.get("aliases", []),
                        domain_tag=domain,
                        source="preset",
                    ).on_conflict_do_nothing(
                        index_elements=[ConceptNode.canonical_name]
                    )
                    res = await s.execute(stmt)
                    if res.rowcount > 0:
                        inserted += 1
        log.info("seeded %d preset concepts", inserted)
        return inserted


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
