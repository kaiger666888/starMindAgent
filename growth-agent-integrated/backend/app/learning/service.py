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
    """三段式概念抽取：候选生成(jieba) -> LLM 精判 -> 降级。

    候选层解决全文覆盖与截断（text[:2000] 旧方案的问题），
    LLM 精判解决"低频但关键"与主题词过滤（纯统计做不到）。
    """
    if not md_content.strip():
        return []

    # -- 第 1 段：候选生成（分块 + jieba TF-IDF/TextRank 双路 + 标题词）--
    from app.learning.keyword_candidates import generate_candidates, fallback_top
    candidates = generate_candidates(md_content)

    # -- 第 2 段：LLM 精判（输入=大纲+候选带证据，非全文盲送）--
    items = None
    if candidates:
        items = await _llm_refine_concepts(md_content, candidates)

    # -- 第 3 段：LLM 失败降级为候选层综合分 top8 --
    if not items:
        log.info("LLM concept refine failed, fallback to candidate top-N")
        items = fallback_top(candidates, n=8)

    log.info("material extract: candidates=%d, items=%d, first=%s",
             len(candidates), len(items), items[0] if items else None)

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
            import json as _json
            from sqlalchemy import text as sql_text
            ids_json = _json.dumps(cids)
            from app.db import is_sqlite
            if is_sqlite():
                # sqlite 无 json 类型，CAST(:ids AS json) 按数字亲和性把 JSON
                # 字符串转成 0（实测根层 ids 被写成整数 0，恢复会话概念全丢）。
                # text() 直传字符串，列声明为 JSON 类型读取时自动反序列化。
                await s.execute(sql_text(
                    "UPDATE qa_step SET extracted_concept_ids = :ids "
                    "WHERE qa_id = :qid"
                ), {"ids": ids_json, "qid": qa_id})
            else:
                await s.execute(sql_text(
                    "UPDATE qa_step SET extracted_concept_ids = CAST(:ids AS jsonb) "
                    "WHERE qa_id = CAST(:qid AS uuid)"
                ), {"ids": ids_json, "qid": qa_id})
    return concepts_out


def _build_outline(md: str, max_headings: int = 30) -> str:
    """文档大纲（H1-H4 全标题），给 LLM 结构感而非全文。"""
    heads = [m.group(0).strip() for m in re.finditer(r"^#{1,4}\s+.+$", md, flags=re.MULTILINE)]
    return "\n".join(heads[:max_headings])


async def _llm_refine_concepts(md_content: str, candidates) -> list[dict] | None:
    """流水线第 2 段：LLM 从候选中精判核心概念。

    输入 = 文档大纲 + 候选列表（带出处/频次证据），全文压到骨架。
    LLM 职责：挑核心、排主题词、补候选外概念、判重要性（三档）。
    """
    from app.inference.backend import default_backend
    backend = default_backend()
    if not hasattr(backend, "complete_text"):
        return None

    outline = _build_outline(md_content)
    # 候选带证据：名字 | 分数 | 频次 | 出现章节（截断防 prompt 膨胀）
    cand_lines = []
    for c in candidates[:50]:
        cand_lines.append(f"- {c.name}｜分{c.score:.2f}｜频{c.freq}｜{c.section_str}")
    cand_text = "\n".join(cand_lines)

    system = (
        "你是知识图谱构建专家。下面给你一篇学习材料的文档大纲和候选概念列表"
        "（候选由统计算法从全文抽取，带分数/频次/出处，分数已对专业术语加权）。"
        "请从中精选 5-15 个【值得学习者下钻探索的核心概念】。\n\n"
        "筛选规则：\n"
        "1. 专业术语优先：学术/技术专有名词 > 泛化普通词。"
        "具体术语如\"一致性哈希\"\"CRDT\"\"Raft\"\"Exactly-Once 语义\"直接入选；"
        "泛词如\"一致性\"\"语义\"\"服务\"\"消息\"默认排除（除非有对应具体术语）。"
        "结构词（\"背景\"\"结论\"\"方法\"）与领域背景词排除。\n"
        "2. 低频但出现在关键章节的概念应保留。\n"
        "3. 英文术语/缩写与中文规范名合并为一条（aliases 互通），"
        "英文碎片（\"once\"\"rebalance\"）还原成完整术语（\"Exactly-Once 语义\"\"消费者组 Rebalance\"）。\n"
        "4. 候选遗漏的核心概念可补充新增（confidence 给 0.5-0.7）。\n\n"
        "输出 JSON 数组，每项（精简，不要定义）：\n"
        '{"name": "概念规范名", "aliases": ["别名"], "confidence": 0.9}\n'
        "只输出 JSON。核心概念 0.9-1.0，次要 0.7-0.8。"
    )
    user = f"文档大纲：\n{outline}\n\n候选概念（按统计分排序）：\n{cand_text}"

    import json as _json, re as _re
    for attempt in range(3):
        try:
            # 网关实测：候选精判规模（~1.5K 输入 + JSON 输出）thinking disabled 也要 ~80s，
            # 90s 贴边会连败两次，放宽到 150s；
            # 长 prompt 会触发 thinking leak（思考吃光 8000 预算、正文 0 字），
            # max_tokens 提到 16000 保正文
            raw = await backend.complete_text(system, user, timeout=240.0, max_tokens=16000)
            if not raw.strip():
                # 网关偶发违反 thinking=disabled：思考吃光 max_tokens、正文 0 字
                # （stop_reason=max_tokens + 仅空 thinking block）。重试通常恢复
                log.warning("LLM refine got empty body (gateway thinking leak), attempt %d", attempt + 1)
                continue
            items = _parse_items_lenient(raw)
            if items:
                return items
        except Exception as e:
            log.warning("LLM concept refine attempt %d failed: %s", attempt + 1, e)
            if attempt < 2:
                import asyncio
                await asyncio.sleep(2)  # 429 退避
    return None


def _parse_items_lenient(raw: str) -> list[dict] | None:
    """容错解析 LLM 输出的 JSON 数组：整体解析失败时逐项提取，坏项丢弃。

    glm 输出的 aliases 偶含未转义引号 -> 整体 json.loads 抛错；
    逐项抢救可保住 90% 概念而不是全量降级。
    """
    import json, re
    if not raw:
        return None
    cleaned = re.sub(r"^```(?:json)?\s*", "", raw.strip(), flags=re.IGNORECASE)
    cleaned = re.sub(r"\s*```\s*$", "", cleaned).strip()
    m = re.search(r"\[[\s\S]*\]", cleaned)
    if not m:
        return None
    body = m.group(0)
    try:
        items = json.loads(body)
        return [it for it in items if isinstance(it, dict) and it.get("name")] or None
    except json.JSONDecodeError:
        pass
    # 逐项提取：{...} 对象正则，单个解析失败跳过
    items = []
    for om in re.finditer(r"\{[^{}]*\}", body):
        try:
            it = json.loads(om.group(0))
            if isinstance(it, dict) and it.get("name"):
                items.append(it)
        except json.JSONDecodeError:
            continue
    return items or None


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
    # query 关键词：jieba 切词（中文问句无空格，re 按标点分词会得到
    # 整句超长 term，子串匹配必 0 命中——自举测试实测定位）。
    qterms = [w for w in re.split(r"[\s，。、；？?]+", query) if len(w) >= 2]
    try:
        import jieba
        jieba_terms = [w for w in jieba.lcut(query) if len(w) >= 2]
        qterms = list(dict.fromkeys(qterms + jieba_terms))
    except ImportError:
        pass
    scored = []
    for i, ch in enumerate(chunks):
        score = sum(1 for t in qterms if t in ch)
        scored.append((score, i, ch))
    scored.sort(key=lambda x: (-x[0], x[1]))
    # 只装 score>=1 的 chunk：零命中时 out 为空 -> 走下面的空返回，
    # 不注入材料开头（材料开头是最高层概括，注入会把回答推向同质化）。
    # 旧实现循环不过滤 score=0，零命中照样按原序塞材料开头（防护死代码）。
    out = []
    total = 0
    for score, i, ch in scored:
        if score < 1:
            break  # 已按分数降序，后面全是 0 分
        if total + len(ch) > max_chars:
            continue  # 超预算的跳过（后面可能更小），不再 break
        out.append(ch)
        total += len(ch)
    # 相关性保底：最高分 chunk 超预算装不下时截断保留它（相关性优先），
    # 而不是退化到材料开头（那是最高层概括内容，与下钻概念无关，
    # 会把每层回答推向"对最高层内容的概括性解读"--同质化根因之一）
    if not out and scored and scored[0][0] >= 1:
        out.append(scored[0][2][:max_chars])
    # 准备度:命中段落数 / 总段落数。query 无有效关键词时退化为 0
    matched = sum(1 for sc, _, _ in scored if sc >= 1)
    # 零命中（或 query 无关键词）：不注入任何材料段落（context_text 为空），
    # 让模型按概念链 + 问句回答，比塞无关的材料开头更好
    if not out or not qterms:
        return {"context_text": "", "snippets": [], "preparation": 0.0,
                "total_chunks": len(chunks), "matched_chunks": matched}
    prep = (matched / len(chunks)) if chunks else 0.0
    ctx_text = "\n\n".join(out)
    return {
        "context_text": ctx_text,
        "snippets": out,
        "preparation": round(prep, 3),
        "total_chunks": len(chunks),
        "matched_chunks": matched,
    }
