"""记忆卡片生成服务：把 QA 问答总结成盲 check 复习卡片。

两条生成入口（同一套 LLM 总结逻辑）：
1. 后台周期扫描（main.py lifespan 挂 worker）：每 30 分钟扫一遍
   全体用户 checked=true 的 QAStep，凡概念尚无卡片则生成。
   checked 是"学习者标记本层已读完/已掌握"的手动信号，是最自然的
   制卡候选（与 007 迁移语义一致）。
2. 阅读区选中正文主动建卡（routes_memory.py /cards/from-selection）：
   用户选中一段觉得值得记的文字，立刻生成一张卡片。

卡片内容（LLM 总结，非原文照搬）：
- question：卡片正面。测试性问题（不是复述原问题），供盲 check 时
  先回忆再看答案印证。
- answer：卡片背面。要点式答案（3-6 条），供印证。

复习状态机（routes_memory.py grade 端点消费）：
- streak：连续"理解"天数；understood 时 +1，≥3 归档（status=archived）
- forgot / retry：streak 清零，due_at = 次日
"""
from __future__ import annotations
import json
import logging
import re
from datetime import datetime, timezone, timedelta

from sqlalchemy import select, func, text as sql_text

from app.db import session_scope, is_sqlite
from app.models.tables import (
    AppUser, QASession, QAStep, ConceptNode, MemoryCard,
)

log = logging.getLogger(__name__)

_CARD_SYSTEM = (
    "你是记忆卡片制作助手。基于给定的问答内容，制作一张用于间隔复习的记忆卡片。"
    "只输出一个 JSON 对象，键为："
    "question(卡片正面的测试性问题——让学习者凭记忆回忆要点，不要照抄原问题，"
    "也不要在问题里泄露答案)，"
    "answer(卡片背面的要点式答案，3-6条，每条一行，用顿号或分号分隔要点，"
    "精炼但信息完整)。"
    "不要输出 JSON 以外的任何文字。"
)

# 已建卡 QA 的重复扫描保护窗口：checked 的层每次扫都会命中，
# 这个窗口只挡"刚扫过无新 checked"的空转，不依赖它去重（去重靠唯一索引）
_SCAN_EMPTY_INTERVAL_S = 1800


async def _find_missing_checked(limit: int = 40) -> list[dict]:
    """找出已 checked 但还没有卡片的 QA 层（全用户）。

    返回 [{user_id, qa_id, session_id, question, answer, concept_id, concept_name}]。
    """
    async with session_scope() as s:
        # 有卡片概念的集合（同 QA 同概念已建卡即跳过，与唯一索引语义一致）
        rows = (
            await s.execute(
                select(
                    QASession.user_id,
                    QAStep.qa_id,
                    QAStep.session_id,
                    QAStep.question,
                    QAStep.answer,
                    QAStep.extracted_concept_ids,
                    QAStep.depth,
                )
                .join(QASession, QAStep.session_id == QASession.session_id)
                .where(QAStep.checked == True)  # noqa: E712
                .where(QAStep.answer.isnot(None))
                .order_by(QAStep.updated_at.desc())
                .limit(limit)
            )
        ).all()
        if not rows:
            return []
        qa_ids = [r.qa_id for r in rows]
        existing = (
            await s.execute(
                select(MemoryCard.qa_id, MemoryCard.concept_id)
                .where(MemoryCard.qa_id.in_(qa_ids))
            )
        ).all()
        done = {(str(r.qa_id), str(r.concept_id) if r.concept_id else None) for r in existing}
        # 概念名批量查（冗余名防概念节点后续被删/合并）
        cids = set()
        for r in rows:
            for c in (r.extracted_concept_ids or []):
                cids.add(str(c))
        name_map: dict[str, str] = {}
        if cids:
            cnodes = (
                await s.execute(
                    select(ConceptNode.concept_id, ConceptNode.canonical_name)
                    .where(ConceptNode.concept_id.in_(list(cids)))
                )
            ).all()
            name_map = {str(n.concept_id): n.canonical_name for n in cnodes}

    out = []
    for r in rows:
        concepts = [str(c) for c in (r.extracted_concept_ids or [])]
        pending = [c for c in concepts if (str(r.qa_id), c) not in done]
        # 该层所有概念都有卡 → 整层跳过；概念层很多时只取前 5 个防刷屏
        if concepts and not pending:
            continue
        out.append({
            "user_id": r.user_id or "default",
            "qa_id": str(r.qa_id),
            "session_id": str(r.session_id),
            "question": r.question,
            "answer": r.answer or "",
            # 逐概念建卡：一层多概念会得到多张卡（每张卡一个概念正面）；
            # 无概念的 checked 层也建一张卡（qa 维度兜底）
            "concept_ids": pending[:5],
            "concept_names": [name_map.get(c, "") for c in pending[:5]],
        })
    return out


async def _llm_make_card(question: str, answer: str, concept_name: str | None) -> tuple[dict, str]:
    """调 LLM 把一段 QA 总结成 {question, answer} 卡片。

    返回 (card_dict, model_name)。失败返回兜底卡片（question=原问题截断）。
    """
    from app.inference.backend import default_backend, OpenAICompatibleBackend
    backend = default_backend()
    model_name = getattr(backend, "model", None) or "stub"

    topic = f"（概念：{concept_name}）" if concept_name else ""
    user = (
        f"以下是学习者的一段问答记录：\n\n"
        f"Q: {question[:600]}\n\nA: {answer[:1500]}\n\n"
        f"请{topic}围绕核心知识点制作记忆卡片。"
    )

    if isinstance(backend, OpenAICompatibleBackend):
        for attempt in range(2):
            try:
                raw = await _chat_text(backend, user)
                parsed = _parse_card_json(raw)
                if parsed:
                    return parsed, model_name
                log.warning("card LLM output unparsable (attempt %d)", attempt + 1)
            except Exception as e:
                log.warning("card LLM summarize failed (attempt %d): %s", attempt + 1, e)
    return _fallback_card(question, answer, concept_name), model_name


async def _chat_text(backend, prompt: str) -> str:
    """非流式调 OpenAI 兼容 chat completions，返回 message.content。"""
    import httpx
    payload = {
        "model": backend.model, "stream": False,
        "messages": [
            {"role": "system", "content": _CARD_SYSTEM},
            {"role": "user", "content": prompt},
        ],
    }
    if hasattr(backend, "thinking_enabled") and not backend.thinking_enabled:
        payload["thinking"] = {"type": "disabled"}
    async with httpx.AsyncClient(timeout=120.0) as c:
        r = await c.post(backend.endpoint,
            headers={"Authorization": f"Bearer {backend.api_key}", "Content-Type": "application/json"},
            json=payload)
        data = r.json()
    return data["choices"][0]["message"].get("content") or ""


def _parse_card_json(raw: str) -> dict | None:
    """从 LLM 输出里抠 {question, answer}。"""
    m = re.search(r"\{[\s\S]*\}", raw)
    if not m:
        return None
    try:
        obj = json.loads(m.group(0))
    except json.JSONDecodeError:
        return None
    q = (obj.get("question") or "").strip()
    a = (obj.get("answer") or "").strip()
    if not q or not a:
        return None
    return {"question": q[:500], "answer": a[:2000]}


def _fallback_card(question: str, answer: str, concept_name: str | None) -> dict:
    """LLM 不可用时的兜底：原问题做正面、原答案要点化做背面。"""
    a = (answer or "").strip()
    # 要点化：按句号/分号切段，取前 6 段
    parts = [p.strip() for p in re.split(r"[。；;]", a) if len(p.strip()) >= 4][:6]
    if not parts:
        parts = [a[:200]] if a else ["（原文无回答）"]
    front = concept_name or (question or "").strip()[:80]
    return {"question": front, "answer": "；".join(parts)}


async def _insert_card(
    user_id: str, concept_id: str | None, concept_name: str,
    qa_id: str | None, session_id: str | None,
    question: str, answer: str, source_answer: str | None,
    generator_model: str,
) -> str | None:
    """插卡（幂等：唯一索引冲突静默跳过）。返回 card_id 或 None。"""
    async with session_scope() as s:
        card = MemoryCard(
            user_id=user_id,
            concept_id=concept_id,
            qa_id=qa_id,
            session_id=session_id,
            concept_name=concept_name or "未命名概念",
            question=question,
            answer=answer,
            source_answer=source_answer,
            generator_model=generator_model,
        )
        s.add(card)
        try:
            await s.flush()
            return str(card.card_id)
        except Exception:
            # 唯一索引冲突（已建卡）或其他约束冲突：整事务回滚即静默跳过
            await s.rollback()
            return None


async def generate_for_checked() -> int:
    """后台周期入口：扫 checked QA 层，逐层逐概念建卡。返回新建卡数。"""
    try:
        pending = await _find_missing_checked()
    except Exception as e:
        log.warning("card scan query failed: %s", e)
        return 0
    if not pending:
        return 0
    created = 0
    for item in pending:
        for cid, cname in zip(item["concept_ids"], item["concept_names"]):
            try:
                card, model = await _llm_make_card(item["question"], item["answer"], cname)
            except Exception as e:
                log.warning("card LLM failed qa=%s concept=%s: %s", item["qa_id"], cname, e)
                continue
            card_id = await _insert_card(
                user_id=item["user_id"], concept_id=cid, concept_name=cname,
                qa_id=item["qa_id"], session_id=item["session_id"],
                question=card["question"], answer=card["answer"],
                source_answer=item["answer"][:4000],
                generator_model=model,
            )
            if card_id:
                created += 1
        if not item["concept_ids"]:
            # 无概念的 checked 层也建一张卡（qa 维度）
            try:
                card, model = await _llm_make_card(item["question"], item["answer"], None)
                card_id = await _insert_card(
                    user_id=item["user_id"], concept_id=None, concept_name="",
                    qa_id=item["qa_id"], session_id=item["session_id"],
                    question=card["question"], answer=card["answer"],
                    source_answer=item["answer"][:4000],
                    generator_model=model,
                )
                if card_id:
                    created += 1
            except Exception as e:
                log.warning("card LLM failed qa=%s (no concept): %s", item["qa_id"], e)
    if created:
        log.info("memory cards generated: %d", created)
    return created


async def generate_from_selection(
    user_id: str, selected_text: str,
    qa_id: str | None = None, concept_id: str | None = None,
    question: str | None = None, answer: str | None = None,
) -> dict:
    """选段主动建卡入口：用选中文字 + 所在层 QA 作上下文生成一张卡。

    - selected_text 是用户选中的正文片段（卡片的核心素材）
    - qa_id 可选：定位所在层，把整层 QA 喂给 LLM 作背景（正面更准）
    - concept_id 可选：归属概念（用于复习队列与概念图联动）
    生成同步返回卡片（前端立刻可见"已入复习队列"）。
    """
    if question is None and qa_id:
        session_id = None
        async with session_scope() as s:
            row = (
                await s.execute(
                    select(QAStep.question, QAStep.answer, QAStep.session_id)
                    .where(QAStep.qa_id == qa_id)
                )
            ).first()
            if row:
                question = row.question
                if answer is None:
                    answer = row.answer
                session_id = str(row.session_id)
    else:
        session_id = None
    concept_name = ""
    if concept_id:
        async with session_scope() as s:
            c = (
                await s.execute(
                    select(ConceptNode.canonical_name)
                    .where(ConceptNode.concept_id == concept_id)
                )
            ).scalar_one_or_none()
            concept_name = c or ""
    # 建卡素材 = 选段为主、整层 QA 为辅
    material_q = question or (concept_name or "选中的知识点")
    material_a = selected_text if not answer else (
        f"{selected_text}\n\n（所在层问答供参考）\nQ: {question}\nA: {answer[:1000]}"
    )
    card, model = await _llm_make_card(material_q, material_a, concept_name or None)
    card_id = await _insert_card(
        user_id=user_id, concept_id=concept_id, concept_name=concept_name,
        qa_id=qa_id, session_id=session_id,
        question=card["question"], answer=card["answer"],
        source_answer=selected_text[:4000],
        generator_model=model,
    )
    if card_id is None:
        return {"created": False, "reason": "duplicate"}
    return {
        "created": True, "card_id": card_id,
        "question": card["question"], "answer": card["answer"],
        "concept_name": concept_name, "generator_model": model,
    }


# ---------------------------------------------------------------------------
# 后台周期 worker（main.py lifespan 启动）
# ---------------------------------------------------------------------------
class CardGenerationWorker:
    """周期扫描 checked QA 生成记忆卡片的进程内 worker。"""

    def __init__(self, interval_s: int = _SCAN_EMPTY_INTERVAL_S):
        self.interval_s = interval_s
        self._task = None
        self._stop = None

    def start(self):
        import asyncio
        self._stop = asyncio.Event()
        self._task = asyncio.create_task(self._run())

    async def stop(self):
        if self._stop:
            self._stop.set()
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except Exception:
                pass
            self._task = None

    async def _run(self):
        import asyncio
        # 启动先歇 60s：避开冷启动 LLM 网关高峰，让主链路先就绪
        try:
            await asyncio.sleep(60)
        except asyncio.CancelledError:
            return
        while not (self._stop and self._stop.is_set()):
            try:
                n = await generate_for_checked()
                if n:
                    log.info("card worker generated %d cards", n)
            except asyncio.CancelledError:
                return
            except Exception as e:
                log.warning("card worker cycle failed: %s", e)
            try:
                await asyncio.sleep(self.interval_s)
            except asyncio.CancelledError:
                return


card_worker = CardGenerationWorker()
