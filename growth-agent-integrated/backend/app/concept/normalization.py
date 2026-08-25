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
    """本地开发桩：用规范名+别名编辑距离综合近似，真实环境可替换为向量召回。

    网关无 /embeddings 接口时用此规则版召回（别名包含优先，编辑距离次之）。
    """
    async def embed(self, text: str) -> list[float]:
        # 把 query 文本塞进 vec[1]，recall 用它做文本相似度
        return [float(len(text)), text]

    async def recall(self, vec: list[float], session_id: str, topk: int) -> list[tuple[str, float]]:
        from app.concept.normalization import _text_similarity
        async with session_scope() as s:
            rows = (
                await s.execute(
                    select(ConceptNode.concept_id, ConceptNode.canonical_name, ConceptNode.aliases)
                    .order_by(ConceptNode.canonical_name).limit(topk * 8)
                )
            ).all()
        out = []
        for r in rows:
            score = _text_similarity(vec[1] if len(vec) > 1 else "", r.canonical_name, r.aliases or [])
            if score > 0.3:  # 下界过滤
                out.append((r.concept_id, round(score, 4)))
        out.sort(key=lambda x: x[1], reverse=True)
        return out[:topk]


def _text_similarity(query: str, canonical: str, aliases: list) -> float:
    """文本相似度：别名精确包含(1.0) > 子串包含(0.9) > 编辑距离比(0-0.8)。"""
    if not query:
        return 0.0
    q = query.lower().strip()
    c = canonical.lower().strip()
    if q == c:
        return 1.0
    # 别名精确匹配
    for a in aliases:
        if q == a.lower().strip():
            return 1.0
    # 子串包含
    if q in c or c in q:
        return 0.9
    for a in aliases:
        a_low = a.lower().strip()
        if q in a_low or a_low in q:
            return 0.9
    # 编辑距离比（Levenshtein）
    dist = _levenshtein(q, c)
    max_len = max(len(q), len(c), 1)
    sim = 1.0 - dist / max_len
    return min(0.8, sim)


def _levenshtein(a: str, b: str) -> int:
    if not a:
        return len(b)
    if not b:
        return len(a)
    prev = list(range(len(b) + 1))
    for i, ca in enumerate(a, 1):
        cur = [i]
        for j, cb in enumerate(b, 1):
            cur.append(min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + (0 if ca == cb else 1)))
        prev = cur
    return prev[-1]


class MockLLMJudge:
    async def judge(self, candidate_name, candidate_canonical, matched_alias, similarity):
        # 桩：相似度越高越倾向合并
        merge = similarity >= 0.85
        return merge, f"mock judge sim={similarity:.3f}"


class LLMJudge:
    """真实灰区判定：用 LLM complete_text 让模型判定两个概念是否同义。

    LLM 不可用时退化为 MockLLMJudge（基于相似度阈值）。
    """
    def __init__(self):
        self._fallback = MockLLMJudge()
        self._cache = {}  # (name, canonical) -> (merge, reason)

    async def judge(self, candidate_name, candidate_canonical, matched_alias, similarity):
        from app.inference.backend import default_backend
        backend = default_backend()
        if not hasattr(backend, "complete_text"):
            return await self._fallback.judge(candidate_name, candidate_canonical, matched_alias, similarity)
        cache_key = (candidate_name, candidate_canonical)
        if cache_key in self._cache:
            return self._cache[cache_key]
        system = (
            "你是概念归一化判定助手。判断两个概念名是否指同一概念（同义词/中英文/缩写）。"
            "只输出 JSON: {\"merge\": true/false, \"reason\": \"一句话\"}。"
        )
        user = (f"候选概念: {candidate_name}\n已存在规范名: {candidate_canonical}"
                f"\n匹配别名: {matched_alias or '无'}\n相似度: {similarity:.3f}"
                f"\n是否合并为同一概念？")
        try:
            import json as _json, re
            raw = await backend.complete_text(system, user, timeout=15.0)
            m = re.search(r"\{[\s\S]*\}", raw)
            if not m:
                return await self._fallback.judge(candidate_name, candidate_canonical, matched_alias, similarity)
            d = _json.loads(m.group(0))
            result = (bool(d.get("merge", False)), str(d.get("reason", "")))
            self._cache[cache_key] = result
            return result
        except Exception:
            return await self._fallback.judge(candidate_name, candidate_canonical, matched_alias, similarity)


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
        import json as _json
        from app.db import is_sqlite
        async with session_scope() as s:
            if is_sqlite():
                # sqlite 无 JSONB @> ：python 端比对（节点量级小，可接受）
                rows = (await s.execute(select(ConceptNode))).scalars()
                for row in rows:
                    if row.canonical_name == name or name in (row.aliases or []):
                        return {"concept_id": row.concept_id,
                                "matched_alias": name,
                                "canonical_name": row.canonical_name}
                return None
            # canonical_name 精确匹配 或 aliases JSONB 包含
            # 用 first() 而非 scalar_one_or_none()：历史数据可能有重复节点
            # （同名 canonical 或多节点别名含同一 name），取第一个匹配即可归一化
            row = (
                await s.execute(
                    select(ConceptNode).where(
                        (ConceptNode.canonical_name == name)
                        # aliases 用 json.dumps 构造（概念名可能含双引号，f-string 拼接
                        # 会产生非法 JSON -> asyncpg InvalidTextRepresentationError）
                        | ConceptNode.aliases.op('@>')(cast(literal(_json.dumps([name])), JSONB))
                    ).limit(1)
                )
            ).scalars().first()
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


normalizer = ConceptNormalizer(embedder=MockEmbedder(), judge=LLMJudge())
