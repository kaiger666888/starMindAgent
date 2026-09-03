"""学习记忆服务：聚合历史 QA + 调 LLM 生成学习画像。

数据双轨：
- 原始：qa_session / qa_step / concept_node（001 表，已存在）
- 画像：user_profile（003 表，LLM 周期总结）

聚合逻辑：
1. 查用户全部 QAStep（question+answer+概念+depth）
2. 拼成 prompt 喂给 OpenAICompatibleBackend 非流式调用
3. glm-5.2 输出 JSONB 画像：mastered/weak/interests/recommendation/summary
4. upsert user_profile
"""
from __future__ import annotations
import json
import logging
from datetime import datetime, timezone
from sqlalchemy import select, func, insert, update, delete
from sqlalchemy.dialects.postgresql import insert as pg_insert

from app.db import session_scope
from app.models.tables import (
    QASession, QAStep, AppUser, UserProfile, ConceptNode, ConceptEdge, LearningMaterial,
)

log = logging.getLogger(__name__)

# 画像总结 prompt（约束模型输出 JSON）
_SYSTEM = (
    "你是学习分析助手。基于用户的历史问答，总结学习画像，"
    "只输出一个 JSON 对象，键为：mastered(已掌握概念名数组), "
    "weak(薄弱/待加强概念名数组), interests(感兴趣方向数组), "
    "recommendation(下一步学习建议，一句话), summary(整体学习画像，2-3句)。"
    "不要输出 JSON 以外的任何文字。"
)


async def list_sessions(user_id: str, limit: int = 50) -> list[dict]:
    """列出用户的会话及每个会话的 QA 概况。"""
    async with session_scope() as s:
        sessions = (
            await s.execute(
                select(QASession)
                .where(QASession.user_id == user_id)
                .order_by(QASession.created_at.desc())
                .limit(limit)
            )
        ).scalars().all()
        if not sessions:
            return []
        session_ids = [s.session_id for s in sessions]
        # 每个会话的 qa 数 + 最后一个问题
        rows = (
            await s.execute(
                select(
                    QAStep.session_id,
                    func.count(QAStep.qa_id).label("cnt"),
                    func.max(QAStep.created_at).label("last_at"),
                )
                .where(QAStep.session_id.in_(session_ids))
                .group_by(QAStep.session_id)
            )
        ).all()
        cnt_map = {r.session_id: r.cnt for r in rows}
        last_map = {r.session_id: r.last_at for r in rows}
        # 每个会话最后一条 question（普通会话的展示标题）
        last_q_rows = (
            await s.execute(
                select(QAStep.session_id, QAStep.question, QAStep.created_at)
                .where(QAStep.session_id.in_(session_ids))
                .order_by(QAStep.session_id, QAStep.created_at.desc())
            )
        ).all()
        last_q = {}
        for r in last_q_rows:
            last_q.setdefault(r.session_id, r.question)
        # 导入文件会话:用 L0 根层(parent_qa_id IS NULL)的 question 作标题,
        # 而非 last_question。根因:drilldown 时 routes_qa.py 把材料上下文拼进
        # 子层 question 存库(【参考学习材料相关段落】...【用户问题】...),
        # last_question 被这个污染串覆盖。L0 根层的 question 才是干净文件名。
        root_q_rows = (
            await s.execute(
                select(QAStep.session_id, QAStep.question)
                .where(QAStep.session_id.in_(session_ids))
                .where(QAStep.parent_qa_id.is_(None))
            )
        ).all()
        root_q = {r.session_id: r.question for r in root_q_rows}
        return [
            {
                "session_id": str(sess.session_id),
                "user_id": sess.user_id,
                "domain_tag": sess.domain_tag,
                "created_at": sess.created_at.isoformat() if sess.created_at else None,
                "qa_count": cnt_map.get(sess.session_id, 0),
                # 导入文件:用 L0 根层干净标题;普通会话:用最后一条 question
                "last_question": root_q.get(sess.session_id) if sess.domain_tag == "imported" else last_q.get(sess.session_id),
            }
            for sess in sessions
        ]


async def get_session_detail(session_id: str) -> dict:
    """单个会话的完整 QA 步骤树。"""
    async with session_scope() as s:
        sess = (
            await s.execute(
                select(QASession).where(QASession.session_id == session_id)
            )
        ).scalar_one_or_none()
        if sess is None:
            return None
        steps = (
            await s.execute(
                select(
                    QAStep.qa_id, QAStep.parent_qa_id, QAStep.question,
                    QAStep.answer, QAStep.status, QAStep.depth,
                    QAStep.extracted_concept_ids, QAStep.created_at,
                    QAStep.checked,
                )
                .where(QAStep.session_id == session_id)
                .order_by(QAStep.created_at)
            )
        ).all()
        return {
            "session_id": str(sess.session_id),
            "user_id": sess.user_id,
            "domain_tag": sess.domain_tag,
            "created_at": sess.created_at.isoformat() if sess.created_at else None,
            "steps": [
                {
                    "qa_id": str(r.qa_id),
                    "parent_qa_id": str(r.parent_qa_id) if r.parent_qa_id else None,
                    "question": r.question,
                    "answer": r.answer,
                    "status": r.status,
                    "depth": r.depth,
                    "extracted_concept_ids": r.extracted_concept_ids or [],
                    "created_at": r.created_at.isoformat() if r.created_at else None,
                    "checked": bool(r.checked),
                }
                for r in steps
            ],
        }


async def delete_session(session_id: str) -> str | None:
    """删除一次学习的全部痕迹。

    qa_step/audit_log 走 qa_session 的 CASCADE；
    concept_edge.session_id 无外键（纯 UUID 列），需手动清；
    导入材料本身保留（它是可复用资产，删足迹≠删材料）。
    返回删除的 session_id；不存在返回 None。
    """
    async with session_scope() as s:
        sess = (
            await s.execute(
                select(QASession).where(QASession.session_id == session_id)
            )
        ).scalar_one_or_none()
        if sess is None:
            return None
        await s.execute(
            delete(ConceptEdge).where(ConceptEdge.session_id == session_id)
        )
        await s.delete(sess)  # qa_step/audit_log 级联
        return str(session_id)


async def _collect_user_qa(user_id: str, limit: int = 80) -> list[dict]:
    """拉用户最近的 QA（question+answer+概念+depth），喂给 LLM。"""
    async with session_scope() as s:
        rows = (
            await s.execute(
                select(
                    QAStep.question, QAStep.answer, QAStep.depth,
                    QAStep.extracted_concept_ids, QAStep.created_at,
                )
                .join(QASession, QAStep.session_id == QASession.session_id)
                .where(QASession.user_id == user_id)
                .order_by(QAStep.created_at.desc())
                .limit(limit)
            )
        ).all()
    # 反转成时间正序，便于模型理解
    rows = list(reversed(rows))
    out = []
    for r in rows:
        concepts = r.extracted_concept_ids or []
        ans = (r.answer or "").strip()
        if len(ans) > 400:
            ans = ans[:400] + "…"
        out.append({
            "question": r.question,
            "answer": ans,
            "depth": r.depth,
            "concepts": concepts,
        })
    return out


async def refresh_profile(user_id: str, force: bool = False) -> dict:
    """拉历史 QA → 调 LLM 总结 → upsert user_profile。

    force=False 时若 profile 未过期（无新 QA）则跳过 LLM 调用。
    """
    # 检查是否需要刷新
    async with session_scope() as s:
        existing = (
            await s.execute(
                select(UserProfile).where(UserProfile.user_id == user_id)
            )
        ).scalar_one_or_none()
    qa_rows = await _collect_user_qa(user_id)
    qa_count = len(qa_rows)
    if qa_count == 0:
        return {"user_id": user_id, "status": "no_data", "qa_count": 0}
    if existing and not force and existing.qa_count == qa_count:
        return {"user_id": user_id, "status": "fresh", "qa_count": qa_count,
                "last_summary_at": existing.last_summary_at.isoformat() if existing.last_summary_at else None}

    # 调 LLM 总结
    profile, model_name = await _llm_summarize(qa_rows)

    # 概念去重计数
    concept_set: set[str] = set()
    for r in qa_rows:
        for c in r["concepts"]:
            concept_set.add(str(c))

    now = datetime.now(timezone.utc)
    async with session_scope() as s:
        stmt = pg_insert(UserProfile).values(
            user_id=user_id,
            profile=profile,
            qa_count=qa_count,
            concept_count=len(concept_set),
            last_summary_at=now,
            summary_model=model_name,
            version=1,
        )
        stmt = stmt.on_conflict_do_update(
            index_elements=[UserProfile.user_id],
            set_={
                "profile": profile,
                "qa_count": qa_count,
                "concept_count": len(concept_set),
                "last_summary_at": now,
                "summary_model": model_name,
                "version": UserProfile.version + 1,
            },
        )
        await s.execute(stmt)
    log.info("profile refreshed user=%s qa=%d concepts=%d", user_id, qa_count, len(concept_set))
    return {"user_id": user_id, "status": "refreshed", "qa_count": qa_count,
            "concept_count": len(concept_set), "last_summary_at": now.isoformat(),
            "summary_model": model_name}


async def _llm_summarize(qa_rows: list[dict]) -> tuple[dict, str]:
    """调 OpenAICompatibleBackend 非流式 chat completions 直接拿文本。

    返回 (profile_dict, model_name)。失败则返回兜底结构。
    注意：不用 backend.extract_only（它返回 ConceptBlock 而非文本）。
    """
    from app.inference.backend import default_backend, OpenAICompatibleBackend
    backend = default_backend()
    model_name = getattr(backend, "model", None) or "stub"

    # 拼用户 QA 摘要喂给模型
    qa_lines = []
    for i, r in enumerate(qa_rows, 1):
        concepts = ",".join(r["concepts"]) if r["concepts"] else "无"
        qa_lines.append(f"{i}. Q: {r['question']}\n   A: {r['answer'] or '(无回答)'}\n   概念: {concepts}  深度: {r['depth']}")
    user_blob = "\n".join(qa_lines)

    prompt = f"以下是该用户的历史问答记录（共{len(qa_rows)}条）：\n\n{user_blob}\n\n请总结该用户的学习画像。"

    if isinstance(backend, OpenAICompatibleBackend):
        try:
            raw = await _chat_text(backend, prompt)
            return _parse_profile_json(raw), model_name
        except Exception as e:
            log.warning("LLM profile summarize failed: %s, fallback", e)
            return _fallback_profile(qa_rows, reason=str(e)), model_name

    # Stub 或其他：返回基于规则的最小画像
    return _fallback_profile(qa_rows, reason="stub backend"), model_name


async def _chat_text(backend, prompt: str) -> str:
    """直接非流式调 OpenAI 兼容 chat completions，返回 message.content。"""
    import httpx
    payload = {
        "model": backend.model, "stream": False,
        "messages": [
            {"role": "system", "content": _SYSTEM},
            {"role": "user", "content": prompt},
        ],
    }
    # 关闭思考：画像总结不需要推理，直接拿正文，秒回
    if hasattr(backend, "thinking_enabled") and not backend.thinking_enabled:
        payload["thinking"] = {"type": "disabled"}
    async with httpx.AsyncClient(timeout=90.0) as c:
        r = await c.post(backend.endpoint,
            headers={"Authorization": f"Bearer {backend.api_key}", "Content-Type": "application/json"},
            json=payload)
        data = r.json()
    # glm-5.2 是思考模型：message.content 是正文，reasoning_content 是思考（丢弃）
    return data["choices"][0]["message"].get("content") or ""


def _parse_profile_json(raw: str) -> dict:
    """从 LLM 输出里抠出 JSON。"""
    import re
    # 找最外层花括号
    m = re.search(r"\{[\s\S]*\}", raw)
    if not m:
        return {"summary": raw[:500], "mastered": [], "weak": [], "interests": [], "recommendation": ""}
    try:
        return json.loads(m.group(0))
    except json.JSONDecodeError:
        return {"summary": m.group(0)[:500], "mastered": [], "weak": [], "interests": [], "recommendation": ""}


def _fallback_profile(qa_rows: list[dict], reason: str = "") -> dict:
    """LLM 不可用时的规则兜底画像。"""
    concepts: dict[str, int] = {}
    for r in qa_rows:
        for c in r["concepts"]:
            concepts[str(c)] = concepts.get(str(c), 0) + 1
    mastered = [c for c, n in concepts.items() if n >= 2]
    weak = [c for c, n in concepts.items() if n == 1]
    return {
        "mastered": mastered,
        "weak": weak,
        "interests": list(concepts.keys())[:5],
        "recommendation": "建议针对薄弱概念（出现1次）做下钻练习",
        "summary": f"共{len(qa_rows)}条问答，涉及{len(concepts)}个概念（LLM总结不可用：{reason}）",
    }


async def get_profile(user_id: str) -> dict | None:
    """读画像，并标记 stale（是否有新 QA 未纳入）。"""
    async with session_scope() as s:
        prof = (
            await s.execute(
                select(UserProfile).where(UserProfile.user_id == user_id)
            )
        ).scalar_one_or_none()
        if prof is None:
            return None
        # 当前 QA 总数
        actual_qa = (
            await s.execute(
                select(func.count(QAStep.qa_id))
                .join(QASession, QAStep.session_id == QASession.session_id)
                .where(QASession.user_id == user_id)
            )
        ).scalar() or 0
    stale = actual_qa != prof.qa_count
    return {
        "user_id": user_id,
        "profile": prof.profile or {},
        "qa_count": prof.qa_count,
        "concept_count": prof.concept_count,
        "last_summary_at": prof.last_summary_at.isoformat() if prof.last_summary_at else None,
        "summary_model": prof.summary_model,
        "stale": stale,
        "actual_qa_count": actual_qa,
    }
