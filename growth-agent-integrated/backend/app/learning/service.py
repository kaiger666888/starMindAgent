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

    # 抽取概念（传原始 content 含 md 标题，降级用标题抽取）
    concepts = await _extract_concepts_from_material(qa_id, material_id, session_id, content)

    return {
        "material_id": material_id, "qa_id": qa_id,
        "title": title, "content": content, "content_plain": content_plain,
        "concepts": concepts,
    }


async def _extract_concepts_from_material(qa_id: str, material_id: str, session_id: str, md_content: str) -> list[dict]:
    """用 LLM 从材料全文抽取概念，归一化，关联到 L0 QAStep。"""
    if not md_content.strip():
        return []
    text = md_content  # 兼容内部用 text 变量名
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
    import json as _json, re as _re
    items = None
    # LLM 抽取（带重试）
    for attempt in range(2):
        try:
            raw = await backend.complete_text(system, user, timeout=30.0)
            cleaned = _re.sub(r"^```(?:json)?\s*", "", raw.strip(), flags=_re.IGNORECASE)
            cleaned = _re.sub(r"\s*```\s*$", "", cleaned).strip()
            m = _re.search(r"\[[\s\S]*\]", cleaned)
            if m:
                items = _json.loads(m.group(0))
                break
        except Exception as e:
            log.warning("material concept extract attempt %d failed: %s", attempt + 1, e)
            if attempt == 0:
                import asyncio
                await asyncio.sleep(2)  # 429 退避

    # LLM 失败时本地规则降级抽取（基于标题 + 高频词）
    if not items:
        log.info("LLM concept extract failed, fallback to local rule-based")
        items = _local_extract_concepts(text)
        # content_plain 已去 md 标题标记，降级在纯文本上跑可能抽不到标题
        # 用原始 content（含 md 标记）再跑一次
        if not items and text:
            # content_plain 短文本降级：取 2-6 字高频中文词（阈值2次）
            import re as _re2
            word_freq = {}
            for m in _re2.finditer(r"[\u4e00-\u9fa5]{2,6}", text):
                w = m.group()
                word_freq[w] = word_freq.get(w, 0) + 1
            for w, freq in sorted(word_freq.items(), key=lambda x: -x[1])[:8]:
                if freq >= 2 and 2 <= len(w) <= 8:
                    items.append({"name": w, "aliases": [], "confidence": min(0.9, 0.5 + freq * 0.1)})

    log.warning("material extract: items=%d, first=%s", len(items), items[0] if items else None)

    if not items:
        return []

    # 归一化（复用 concept normalizer）
    from app.concept.normalization import normalizer
    from app.schemas import ConceptItem
    from app.models.tables import QASession
    concepts_out = []
    async with session_scope() as s:
        for item in items:
            if not item.get("name"):
                continue
            try:
                # normalize 期望 ConceptItem 对象（有 .name 属性），不是 dict
                ci = ConceptItem(name=item["name"], aliases=item.get("aliases", []),
                                 confidence=float(item.get("confidence", 0.7)))
                resolved = await normalizer.normalize(ci, qa_id, session_id)
                concepts_out.append(resolved)
            except Exception as e:
                log.warning("normalize %s failed: %s", item.get("name"), e)
        cids = [c["concept_id"] for c in concepts_out if c.get("concept_id")]
        if cids:
            from sqlalchemy import text as sql_text
            ids_json = _json.dumps(cids)
            await s.execute(sql_text(
                "UPDATE qa_step SET extracted_concept_ids = CAST(:ids AS jsonb) "
                "WHERE qa_id = CAST(:qid AS uuid)"
            ), {"ids": ids_json, "qid": qa_id})
    return concepts_out


def _local_extract_concepts(text: str) -> list[dict]:
    """LLM 不可用时本地规则抽取：取 markdown 标题词 + 高频名词短语。

    简单但有效：md 的 ## 标题通常是核心概念。
    """
    import re
    items = []
    seen = set()
    # 1. md 标题作概念（最可靠）
    for m in re.finditer(r"^#{1,3}\s+(.+)$", text, re.MULTILINE):
        title = m.group(1).strip()
        # 去标题里的标点/连词
        for part in re.split(r"[·、，,/（）()【】\[\]：:]", title):
            part = part.strip()
            if 2 <= len(part) <= 12 and part not in seen:
                seen.add(part)
                items.append({"name": part, "aliases": [], "confidence": 0.8})
    # 2. 高频中文词（2-4字，出现≥3次）
    word_freq = {}
    for m in re.finditer(r"[\u4e00-\u9fa5]{2,4}", text):
        w = m.group()
        if len(w) >= 2:
            word_freq[w] = word_freq.get(w, 0) + 1
    for w, freq in sorted(word_freq.items(), key=lambda x: -x[1])[:10]:
        if freq >= 3 and w not in seen and 2 <= len(w) <= 8:
            seen.add(w)
            items.append({"name": w, "aliases": [], "confidence": min(0.9, 0.5 + freq * 0.05)})
    return items[:12]


async def get_material_context(material_id: str, query: str, max_chars: int = 2000) -> str:
    """下钻/问答时，从材料里检索与 query 相关的段落，注入 prompt 上下文。

    简单实现：按 query 关键词分句匹配，取最相关段落。生产可换 embedding 检索。
    """
    result = await get_material_context_detail(material_id, query, max_chars)
    return result["context_text"]


async def get_material_context_detail(material_id: str, query: str, max_chars: int = 2000) -> dict:
    """下钻/问答时，从材料里检索与 query 相关的段落，返回结构化结果。

    返回:
      {
        "context_text": str,      # 拼接好的上下文文本（注入 prompt 用，向后兼容）
        "snippets": list[str],    # 命中的相关段落（前端展示用，已按相关度排序）
        "preparation": float,    # 准备度 0-1：命中段落数 / 总段落数（前端进度条用）
        "total_chunks": int,     # 材料总段落数
        "matched_chunks": int,   # 命中（score>=1）的段落数
      }
    无材料/空内容时返回空结果（preparation=0）。
    """
    empty = {"context_text": "", "snippets": [], "preparation": 0.0,
            "total_chunks": 0, "matched_chunks": 0}
    async with session_scope() as s:
        from sqlalchemy import select as _sel
        mat = (await s.execute(_sel(LearningMaterial).where(LearningMaterial.material_id == material_id))).scalar_one_or_none()
        if not mat:
            return empty
        plain = mat.content_plain or ""
    if not plain:
        return empty
    # 按句/段切分
    chunks = re.split(r"\n(?=\S)", plain)
    if not chunks:
        return {"context_text": plain[:max_chars], "snippets": [plain[:max_chars]],
                "preparation": 1.0, "total_chunks": 1, "matched_chunks": 1}
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
    # 准备度:命中段落数 / 总段落数。query 无有效关键词时退化为 0
    matched = sum(1 for sc, _, _ in scored if sc >= 1)
    prep = (matched / len(chunks)) if (qterms and chunks) else 0.0
    ctx_text = "\n\n".join(out) if out else plain[:max_chars]
    snippets = out if out else [plain[:max_chars]]
    return {
        "context_text": ctx_text,
        "snippets": snippets,
        "preparation": round(prep, 3),
        "total_chunks": len(chunks),
        "matched_chunks": matched,
    }
