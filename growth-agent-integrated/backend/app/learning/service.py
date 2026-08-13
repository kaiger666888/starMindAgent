"""学习材料服务：导入 markdown，解析，作探索根 + 上下文注入。

导入后：
- 文件存为 learning_material（content=md 原文，content_plain=纯文本）
- 创建一个 QAStep 作根层（L0），question=title，answer=content_plain
- LLM 自动从全文抽取概念，内联到正文
- 子层下钻/问答时，文件相关段落注入 prompt 上下文
"""
from __future__ import annotations
import re
import logging
from sqlalchemy import select, text
from sqlalchemy.dialects.postgresql import insert as pg_insert

from app.db import session_scope
from app.models.tables import LearningMaterial, QAStep, AppUser, ConceptNode
from datetime import datetime, timezone

log = logging.getLogger(__name__)


def _strip_markdown(md: str) -> str:
    """去 md 标记转纯文本（供概念抽取/检索）。"""
    # 去代码块
    s = re.sub(r"```[\s\S]*?```", "", md)
    # 去行内代码
    s = re.sub(r"`([^`]+)`", r"\1", s)
    # 去标题标记
    s = re.sub(r"^#{1,6}\s*", "", s, flags=re.MULTILINE)
    # 去链接，保留文本
    s = re.sub(r"\[([^\]]+)\]\([^)]+\)", r"\1", s)
    # 去图片
    s = re.sub(r"!\[([^\]]*)\]\([^)]+\)", r"\1", s)
    # 去粗体/斜体
    s = re.sub(r"\*{1,3}([^*]+)\*{1,3}", r"\1", s)
    # 去引用标记
    s = re.sub(r"^>\s*", "", s, flags=re.MULTILINE)
    # 去列表标记
    s = re.sub(r"^[\s]*[-*+]\s+", "", s, flags=re.MULTILINE)
    s = re.sub(r"^[\s]*\d+\.\s+", "", s, flags=re.MULTILINE)
    # 去水平线
    s = re.sub(r"^---+$", "", s, flags=re.MULTILINE)
    return s.strip()


async def import_markdown(user_id: str, title: str, content: str) -> dict:
    """导入 md 文件：存材料 + 建 L0 根 QAStep + 抽取概念。

    返回 {material_id, qa_id, title, content_plain}。
    """
    content_plain = _strip_markdown(content)
    now = datetime.now(timezone.utc)

    async with session_scope() as s:
        # upsert app_user
        stmt = pg_insert(AppUser).values(user_id=user_id, last_active_at=now).on_conflict_do_update(
            index_elements=[AppUser.user_id], set_={"last_active_at": now}
        )
        await s.execute(stmt)
        # 存材料
        mat = LearningMaterial(
            user_id=user_id, title=title, content=content,
            content_plain=content_plain, size_bytes=len(content.encode("utf-8")),
        )
        s.add(mat)
        await s.flush()
        material_id = str(mat.material_id)
        # 建 session（绑定 user_id，L0 根层挂此 session）
        from app.models.tables import QASession
        sess = QASession(user_id=user_id, domain_tag="imported")
        s.add(sess)
        await s.flush()
        session_id = sess.session_id
        # 建 L0 根 QAStep（不推理，直接 waiting）
        qa = QAStep(
            session_id=session_id,
            question=title, answer=content_plain,
            status="waiting", depth=0,  # L0 depth=0
            material_id=material_id,
        )
        s.add(qa)
        await s.flush()
        qa_id = str(qa.qa_id)

    log.info("imported material=%s qa=%s title=%s size=%d", material_id, qa_id, title, len(content))

    # 抽取概念（异步，不阻塞导入返回）
    concepts = await _extract_concepts_from_material(qa_id, material_id, content_plain)

    return {
        "material_id": material_id, "qa_id": qa_id,
        "title": title, "content": content, "content_plain": content_plain,
        "concepts": concepts,
    }


async def _extract_concepts_from_material(qa_id: str, material_id: str, text: str) -> list[dict]:
    """用 LLM 从材料全文抽取概念，归一化，关联到 L0 QAStep。"""
    if not text.strip():
        return []
    from app.inference.backend import default_backend
    backend = default_backend()
    if not hasattr(backend, "complete_text"):
        return []
    system = (
        "从下面这段学习材料中抽取 5-12 个关键概念。用 JSON 数组返回，"
        "每项 {name, aliases:[], confidence:0.0-1.0}。"
        "name 用概念规范名(2-6字)，aliases 含中英文/缩写别名。只输出 JSON。"
    )
    user = f"学习材料标题/摘要：\n{text[:2000]}"
    try:
        import json as _json, re as _re
        raw = await backend.complete_text(system, user, timeout=30.0)
        cleaned = _re.sub(r"^```(?:json)?\s*", "", raw.strip(), flags=_re.IGNORECASE)
        cleaned = _re.sub(r"\s*```\s*$", "", cleaned).strip()
        m = _re.search(r"\[[\s\S]*\]", cleaned)
        if not m:
            return []
        items = _json.loads(m.group(0))
    except Exception as e:
        log.warning("material concept extract failed: %s", e)
        return []

    # 归一化（复用 concept normalizer）
    from app.concept.normalization import normalizer
    from app.models.tables import QASession
    concepts_out = []
    async with session_scope() as s:
        # L0 QAStep 没 session_id，归一化用 material_id 临时作 session
        # 实际上归一化只查全局 concept_node，不依赖 session
        for item in items:
            if not item.get("name"):
                continue
            try:
                resolved = await normalizer.normalize(item, qa_id, material_id)
                concepts_out.append(resolved)
            except Exception as e:
                log.warning("normalize %s failed: %s", item.get("name"), e)
        # 把 concept_ids 存进 L0 QAStep.extracted_concept_ids
        cids = [c["concept_id"] for c in concepts_out if c.get("concept_id")]
        if cids:
            await s.execute(text(
                "UPDATE qa_step SET extracted_concept_ids = :ids::jsonb "
                "WHERE qa_id = CAST(:id AS uuid)"
            ).bindparams(ids=_json.dumps(cids), id=qa_id))
    return concepts_out


async def get_material_context(material_id: str, query: str, max_chars: int = 2000) -> str:
    """下钻/问答时，从材料里检索与 query 相关的段落，注入 prompt 上下文。

    简单实现：按 query 关键词分句匹配，取最相关段落。生产可换 embedding 检索。
    """
    async with session_scope() as s:
        from sqlalchemy import select as _sel
        mat = (await s.execute(_sel(LearningMaterial).where(LearningMaterial.material_id == material_id))).scalar_one_or_none()
        if not mat:
            return ""
        plain = mat.content_plain or ""
    if not plain:
        return ""
    # 按句/段切分
    chunks = re.split(r"\n(?=\S)", plain)
    if not chunks:
        return plain[:max_chars]
    # query 关键词
    qterms = [w for w in re.split(r"[\s，。、；]+", query) if len(w) >= 2]
    scored = []
    for i, ch in enumerate(chunks):
        score = sum(1 for t in qterms if t in ch)
        scored.append((score, i, ch))
    scored.sort(key=lambda x: (-x[0], x[1]))
    out = []
    total = 0
    for score, i, ch in scored:
        if total + len(ch) > max_chars:
            break
        out.append(ch)
        total += len(ch)
    return "\n\n".join(out) if out else plain[:max_chars]
