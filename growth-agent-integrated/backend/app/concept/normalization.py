"""概念归一化三级流水线（P1，但是 concept 服务质量的关键）。

技术架构文档第五节：
  抽取概念 -> 别名表精确匹配(命中->合并)
            -> Embedding 召回(超阈值->合并 / 灰色区间->LLM二次判定)
            -> LLM 二次判定(判定合并->合并 / 判定保留->保持独立)
  -> 写 Audit Log -> 支持用户撤销

阈值封装为独立服务 (thresholds)，便于灰度调参。
所有决策只追加写入 audit_log（归一化决策 + merge/undo 动作同通道）。
"""
from __future__ import annotations
import logging
from typing import Optional, Protocol

from sqlalchemy import select, func, cast, literal
from sqlalchemy.dialects.postgresql import JSONB

from app.db import session_scope
from app.models.tables import ConceptNode, AuditLog
from app.concept.thresholds import threshold_service, Thresholds

log = logging.getLogger(__name__)


class Embedder(Protocol):
    """Embedding 召回依赖（数据产品经理 / 平台提供）。"""
    async def embed(self, text: str) -> list[float]: ...
    async def recall(self, vec: list[float], session_id: str, topk: int) -> list[tuple[str, float]]: ...


class LLMJudge(Protocol):
    """灰区 LLM 二次判定依赖（推理框架工程师提供轻量调用）。"""
    async def judge(self, candidate_name: str, candidate_canonical: str,
                   matched_alias: str, similarity: float) -> tuple[bool, str]:
        """返回 (是否合并, 理由)。"""


class MockEmbedder:
    """本地开发桩：用规范名编辑距离近似，真实环境替换为向量召回。"""
    async def embed(self, text: str) -> list[float]:
        return [float(len(text))]

    async def recall(self, vec: list[float], session_id: str, topk: int) -> list[tuple[str, float]]:
        async with session_scope() as s:
            rows = (
                await s.execute(
                    select(ConceptNode.concept_id, ConceptNode.canonical_name)
                    .where(ConceptNode.source != "preset")  # 桩：召回已存在的
                    .order_by(ConceptNode.canonical_name).limit(topk * 4)
                )
            ).all()
        out = []
        for r in rows:
            score = 1.0 - abs(len(r.canonical_name) - vec[0]) / max(vec[0], 1.0)
            out.append((r.concept_id, round(max(0.0, score), 4)))
        out.sort(key=lambda x: x[1], reverse=True)
        return out[:topk]


class MockLLMJudge:
    async def judge(self, candidate_name, candidate_canonical, matched_alias, similarity):
        # 桩：相似度越高越倾向合并
        merge = similarity >= 0.85
        return merge, f"mock judge sim={similarity:.3f}"


class ConceptNormalizer:
    """三级串行归一化流水线。"""

    def __init__(self, embedder: Embedder | None = None, judge: LLMJudge | None = None):
        self.embedder = embedder or MockEmbedder()
        self.judge = judge or MockLLMJudge()

    async def normalize(self, item, qa_id: str, session_id: str) -> dict:
        """对单个抽取概念跑三级流水线，返回归一化后的 concept 引用。

        item: schemas.ConceptItem
        """
        t: Thresholds = threshold_service.get()
        name = item.name

        # —— 第一级：别名精确匹配 ——
        hit = await self._alias_exact_match(name, session_id)
        if hit:
            await self._audit(qa_id, session_id, name, hit["matched_alias"],
                              None, "merge", "alias_exact_match")
            return self._resolve(hit["concept_id"], item)

        # —— 第二级：embedding 召回 ——
        vec = await self.embedder.embed(name)
        candidates = await self.embedder.recall(vec, session_id, t.embedding_recall_topk)
        if candidates:
            best_id, best_sim = candidates[0]
            if best_sim >= t.embedding_high:
                await self._audit(qa_id, session_id, name, None,
                                  best_sim, "merge", f"embedding_high({best_sim:.3f})")
                return self._resolve(best_id, item)
            if best_sim < t.embedding_low:
                # 低于下界：保持独立，不进 LLM
                node = await self._create_node(item, session_id)
                await self._audit(qa_id, session_id, name, None,
                                  best_sim, "keep", f"embedding_low({best_sim:.3f})")
                return self._resolve(node.concept_id, item)

            # —— 第三级：灰区 LLM 二次判定 ——
            if t.llm_gray_zone_enabled:
                existing = await self._get_name(best_id)
                merge, verdict = await self.judge.judge(
                    name, existing, None, best_sim
                )
                if merge:
                    # 合并：把新概念并入已有节点（别名追加）
                    merged_id = await self._merge_into(best_id, name, item.aliases)
                    await self._audit(qa_id, session_id, name, None,
                                      best_sim, "merge", verdict,
                                      survivor_id=merged_id, absorbed_name=name)
                    return self._resolve(merged_id, item)
                await self._audit(qa_id, session_id, name, None,
                                  best_sim, "keep", verdict)

        # 全部未命中 -> 新建独立概念节点
        node = await self._create_node(item, session_id)
        return self._resolve(node.concept_id, item)

    async def match_existing_only(self, name: str, session_id: str) -> dict | None:
        """降级模式：只标注已有概念，不新建（膨胀超限时用）。"""
        hit = await self._alias_exact_match(name, session_id)
        if hit:
            return self._resolve(hit["concept_id"], None)
        vec = await self.embedder.embed(name)
        candidates = await self.embedder.recall(vec, session_id, 1)
        if candidates and candidates[0][1] >= 0.85:
            return self._resolve(candidates[0][0], None)
        return None

    # —— 内部 ——
    async def _alias_exact_match(self, name: str, session_id: str) -> dict | None:
        async with session_scope() as s:
            # canonical_name 精确匹配 或 aliases JSONB 包含
            row = (
                await s.execute(
                    select(ConceptNode).where(
                        (ConceptNode.canonical_name == name)
                        | ConceptNode.aliases.op('@>')(cast(literal(f'["{name}"]'), JSONB))
                    )
                )
            ).scalar_one_or_none()
            if row:
                return {"concept_id": row.concept_id,
                        "matched_alias": name,
                        "canonical_name": row.canonical_name}

    async def _create_node(self, item, session_id: str) -> ConceptNode:
        async with session_scope() as s:
            node = ConceptNode(
                canonical_name=item.name,
                aliases=list(item.aliases),
                source="llm_extracted",
            )
            s.add(node)
            await s.flush()
            return node

    async def _merge_into(self, survivor_id: str, name: str, aliases: list[str]) -> str:
        """把 name 作为新别名并入 survivor 节点（不删 survivor，只扩 aliases）。"""
        async with session_scope() as s:
            row = (
                await s.execute(select(ConceptNode).where(ConceptNode.concept_id == survivor_id))
            ).scalar_one()
            cur = set(row.aliases or [])
            cur.add(name)
            cur.update(aliases)
            row.aliases = sorted(cur)
            await s.flush()
            return survivor_id

    async def _get_name(self, concept_id: str) -> str:
        async with session_scope() as s:
            row = (
                await s.execute(select(ConceptNode.canonical_name).where(ConceptNode.concept_id == concept_id))
            ).scalar_one()
            return row

    async def _audit(self, qa_id, session_id, candidate_name, matched_alias,
                     similarity, action, llm_verdict, *, survivor_id=None, absorbed_name=None):
        async with session_scope() as s:
            s.add(AuditLog(
                qa_id=qa_id, session_id=session_id,
                candidate_name=candidate_name, matched_alias=matched_alias,
                similarity_score=similarity, action=action, llm_verdict=llm_verdict,
                survivor_id=survivor_id,
                payload={"absorbed_name": absorbed_name} if absorbed_name else {},
            ))
            await s.flush()

    @staticmethod
    def _resolve(concept_id: str, item) -> dict:
        return {
            "concept_id": concept_id,
            "canonical_name": item.name if item else None,
            "aliases": list(item.aliases) if item else [],
            "confidence": item.confidence if item else 0.0,
            "relation_type": item.relation_type if item else "related",
        }


normalizer = ConceptNormalizer()
