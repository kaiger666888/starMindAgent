"""候选概念生成层（三段式关键词抽取流水线的第 1 段）。

设计（基于调研结论）：
  全文 md（去代码块噪声）
    -> 按标题结构分块（每块 ≤~1500 字）
    -> 每块 jieba TF-IDF + TextRank 双路抽取（限名词词性，压虚词）
    -> ∪ 标题词 ∪ 中英混排词（正则白名单）
    -> 合并去重 -> 候选列表（带出处章节/频次，供 LLM 精判有证据地打分）

解决三个问题：
  1. text[:2000] 截断 -> 分块全覆盖
  2. LLM 只看摘要 -> 候选层不受 LLM 上下文限制
  3. 高频≠关键 -> TF-IDF 的 IDF 压通用词 + TextRank 图结构压虚词；
     低频关键概念由标题词路径保底
"""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field

log = logging.getLogger(__name__)

# jieba 懒加载（首次 ~0.4s 建缓存，之后毫秒级）
_jieba_ready = False


def _ensure_jieba():
    global _jieba_ready
    if not _jieba_ready:
        import jieba  # noqa: F401
        _jieba_ready = True


# 名词类词性白名单：压掉"我们/可以/系统"这类虚词与泛动词
_NOUN_POS = ("n", "nr", "ns", "nt", "nz", "vn", "eng")
# 单英文词元白名单（中英混排技术文档："Agent"、"RAG"、"Transformer"）
_EN_TERM = re.compile(r"[A-Za-z][A-Za-z0-9+.-]{1,19}")
# 中文概念词：2-6 字
_ZH_TERM = re.compile(r"[\u4e00-\u9fa5]{2,6}")
# 通用停用概念（IDF 压不住的领域泛词，高频但无信息量）
_STOP_CONCEPTS = {
    "我们", "可以", "如果", "一个", "这个", "以及", "对于", "通过", "进行",
    "系统", "问题", "方法", "方面", "内容", "情况", "结果", "过程", "目前",
    "不同", "相同", "以上", "如下", "例如", "比如", "作者", "用户", "文章",
    "领域", "技术", "研究", "分析", "实现", "提供", "支持", "工具", "平台",
    "数据", "信息", "方式", "能力", "发展", "应用", "价值", "目标", "核心",
    # 分布式/后端文档高频泛词（实测补充）
    "服务", "语义", "消息", "设计", "节点", "入门", "部分", "结论", "背景",
    "性能", "实践", "场景", "方案", "架构", "逻辑", "结构", "场景", "指标",
    "级别", "控制", "处理", "机制", "模型", "软件", "程序", "环境", "配置",
}


@dataclass
class Candidate:
    """候选概念：名字 + 综合分 + 证据（出处章节、频次）。"""
    name: str
    score: float = 0.0           # TF-IDF + TextRank 归一化综合分
    sections: list[str] = field(default_factory=list)  # 出现的标题章节
    freq: int = 0                # 全文出现次数
    from_title: bool = False     # 是否来自标题（低频关键概念的保底路径）

    @property
    def section_str(self) -> str:
        return "、".join(self.sections[:3]) or "全文"


def _split_sections(md: str) -> list[tuple[str, str]]:
    """按 md 标题切块：返回 [(标题, 块文本)]。

    无标题文档整篇作一块。代码块先剔除（对关键词是噪声）。
    """
    s = re.sub(r"```[\s\S]*?```", "", md)
    # 找所有标题位置
    heads = [(m.start(), m.group(0).strip().lstrip("#").strip())
             for m in re.finditer(r"^#{1,6}\s+.+$", s, flags=re.MULTILINE)]
    if not heads:
        return [("全文", s)]
    sections = []
    for i, (pos, title) in enumerate(heads):
        end = heads[i + 1][0] if i + 1 < len(heads) else len(s)
        body = s[pos:end]
        sections.append((title or "全文", body))
    # 超长块再按 ~1500 字切（jieba 不限长，但分块让低频概念有机会进入 topK）
    out = []
    for title, body in sections:
        if len(body) <= 1500:
            out.append((title, body))
        else:
            for j in range(0, len(body), 1500):
                out.append((title, body[j:j + 1500]))
    return out


def _term_freq(text: str, term: str) -> int:
    return text.count(term)


def _term_boost(name: str) -> float:
    """专业术语形态学加分（用户反馈：术语应优先于泛词）。

    术语的可判别特征（词性无法区分"一致性"和"一致性哈希"，都是 n）：
    - 全大写缩写（CAP/RRF/PRM）：+0.60 强信号
    - 首字母大写专名（Redis/Kafka/Paxos）：+0.35
    - 连接词组（Exactly-Once）：+0.15
    - 中英复合（CAP定理/Quorum机制）：+0.40~0.45
    - 中文学术复合词 4-8 字（一致性哈希/向量时钟）：+0.10
    - 全小写英文词（once/rebalance）与 2-3 字泛名词：+0.00
      （"exactly-once"整体是术语，被切碎的"once"不是；碎片不加权）
    """
    b = 0.0
    has_en = bool(re.search(r"[A-Za-z]", name))
    if has_en:
        if re.fullmatch(r"[A-Z]{2,6}", name):
            b += 0.60
        elif re.match(r"^[A-Z]", name):
            b += 0.35
    if "-" in name:
        b += 0.15
    zh = re.findall(r"[\u4e00-\u9fa5]", name)
    if len(zh) >= 2 and has_en:
        b += 0.05
    elif 4 <= len(zh) <= 8:
        b += 0.10
    return b


def generate_candidates(md: str, topk_per_chunk: int = 12, max_candidates: int = 60) -> list[Candidate]:
    """生成候选概念列表（流水线第 1 段）。

    双路：TF-IDF（IDF 压通用词）+ TextRank（图中心性压虚词），
    分块各取 topk 合并，∪ 标题词 ∪ 中英混排词。
    """
    if not md.strip():
        return []
    _ensure_jieba()
    import jieba.analyse as ja

    sections = _split_sections(md)
    # 章节名 -> 覆盖的原文块（频次统计用整篇原文）
    full_text = re.sub(r"```[\s\S]*?```", "", md)

    cands: dict[str, Candidate] = {}
    merged_scores: dict[str, float] = {}

    def _add(name: str, score: float, section: str, from_title: bool = False):
        name = name.strip()
        if not name or name.lower() in _STOP_CONCEPTS or len(name) < 2:
            return
        c = cands.get(name)
        if c is None:
            c = Candidate(name=name)
            cands[name] = c
            merged_scores[name] = 0.0
        if score > 0:
            merged_scores[name] = max(merged_scores[name], score)
        if section and section not in c.sections:
            c.sections.append(section)
        if from_title:
            c.from_title = True

    for title, body in sections:
        if len(body.strip()) < 10:
            continue
        # 双路抽取（名词词性过滤）
        try:
            tfidf = ja.extract_tags(body, topK=topk_per_chunk, withWeight=True,
                                    allowPOS=_NOUN_POS)
        except Exception as e:  # jieba 偶发（空输入等）
            log.debug("tfidf failed on chunk: %s", e)
            tfidf = []
        try:
            trank = ja.textrank(body, topK=topk_per_chunk, withWeight=True,
                                allowPOS=_NOUN_POS)
        except Exception as e:
            log.debug("textrank failed on chunk: %s", e)
            trank = []
        # 归一化后取大者（两路分值体系不同，chunk 内归一）
        def _norm(pairs):
            if not pairs:
                return {}
            mx = max(w for _, w in pairs) or 1.0
            return {t: w / mx for t, w in pairs}
        tf_n, tr_n = _norm(tfidf), _norm(trank)
        names = set(tf_n) | set(tr_n)
        for n in names:
            _add(n, max(tf_n.get(n, 0.0), tr_n.get(n, 0.0)), title)

    # 标题词路径：md 标题本身就是作者强调的概念（低频关键概念保底）
    for title, _ in sections:
        for part in re.split(r"[·、，,/（）()【】\[\]：:？?！!]", title):
            part = part.strip()
            if 2 <= len(part) <= 12 and part.lower() not in _STOP_CONCEPTS:
                _add(part, 0.6, title, from_title=True)
        # 标题里的英文词元
        for en in _EN_TERM.findall(title):
            _add(en, 0.5, title, from_title=True)

    # 频次（全文口径）+ 术语加权
    for c in cands.values():
        c.freq = _term_freq(full_text, c.name)
        c.score = merged_scores[c.name]
        # 标题词加权：作者把它写进标题 = 强信号
        if c.from_title:
            c.score = max(c.score, 0.65) + 0.1
        # 专业术语优先：形态学加分（缩写/英文/复合词 > 泛名词）
        c.score += _term_boost(c.name)

    # 排序：综合分为主，同分低频优先（低频但进候选的多是标题/英文概念）
    ranked = sorted(cands.values(),
                    key=lambda c: (-c.score, -min(c.freq, 10)))
    return ranked[:max_candidates]


def fallback_top(candidates: list[Candidate], n: int = 8) -> list[dict]:
    """LLM 不可用时的降级取 top-N（流水线第 3 段）。

    排序：综合分 + 频次对数微调（频次保证基础可靠性，
    但对数让低分的标题/英文概念不会被高频词完全淹没）。
    """
    import math
    scored = []
    for c in candidates:
        s = c.score + 0.15 * math.log1p(c.freq)
        if c.from_title:
            s += 0.2
        scored.append((s, c))
    scored.sort(key=lambda x: -x[0])
    return [{"name": c.name, "aliases": [], "confidence": round(min(0.9, 0.45 + s * 0.4), 2)}
            for s, c in scored[:n]]
