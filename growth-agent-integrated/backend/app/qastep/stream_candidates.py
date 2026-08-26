"""流式增量候选概念抽取（阅读时动态高亮用）。

复用 learning/keyword_candidates.py 的 jieba 双路抽取思路，但面向流式场景：
- 输入是逐 delta 的正文片段，不是整篇 md；
- 每 ~120 字（句界对齐）跑一次，只抽新增词，推给前端；
- 事件量控制：一层最多推 3 批、共 12 个候选（权威 concepts 到达后
  前端会切回权威列表，候选只是阅读期间的临时高亮）；
- 纯本地 CPU（jieba 已建缓存时 ms 级），无 LLM 调用 -- 不碰网关
  OOM 放大器。
"""
from __future__ import annotations

import logging
import re

log = logging.getLogger(__name__)

# 复用候选层的停用词与术语加分（保持两处候选口径一致）
from app.learning.keyword_candidates import _STOP_CONCEPTS, _term_boost

# 每次抽取的正文累积阈值（字），到句界再切。
# 正文预算 ~500 字：120 只够切 2 批，尾部 1/3 的概念全丢；
# 降到 80 让 3-4 批均匀覆盖全层。
_CHUNK = 80
# 一层最多推送批次 / 候选总数
_MAX_BATCHES = 4
_MAX_TOTAL = 12

_EN_TERM = re.compile(r"[A-Za-z][A-Za-z0-9+.-]{1,19}")
_ZH_TERM = re.compile(r"[\u4e00-\u9fa5]{2,8}")
# 「X」引号词（下钻问句里用户点名的概念）
_QUOTED = re.compile(r"「([^「」]{2,12})」")
# 句界：句号/问号/叹号/分号/换行
_SENT_END = re.compile(r"[。！？；\n]")
# 名词类词性集合（bigram 合成的词性闸门）
_NOUN_POS_SET = {"n", "nr", "ns", "nt", "nz", "vn", "eng"}
# 功能字黑名单：jieba 对未登录串的词性兜底是 n，'集是'这类碎片会漏过
# 词性闸门；含功能字的合成词一律不是概念
_FUNC_CHARS = set("是的了在和与及或将其被把为有时对会让到从被这那也有")


class StreamCandidateExtractor:
    def __init__(self, question: str = ""):
        self._buf = ""
        self._pushed: set[str] = set()
        self._batches = 0
        self._total = 0
        # 问题里用户显式指名的词不进候选：英文术语 + 「X」引号词
        # （下钻问句格式是「深入解释「X」…」，X 就是要找的概念，
        # 前端种子词通道会高亮它）。不收任意 2-8 字中文串--
        # 会把"评测中的保留集"这类问句碎片当种子，误压正文候选。
        q = question or ""
        for t in _EN_TERM.findall(q):
            self._pushed.add(t.lower())
        for m in _QUOTED.finditer(q):
            for t in _ZH_TERM.findall(m.group(1)):
                self._pushed.add(t)

    def feed(self, delta: str) -> list[dict] | None:
        """喂入正文增量，攒到阈值在句界切出一段跑抽取。

        返回新增候选 [{name, confidence}] 或 None（无新增/已达上限）。
        """
        if self._batches >= _MAX_BATCHES or self._total >= _MAX_TOTAL:
            return None
        self._buf += delta
        if len(self._buf) < _CHUNK:
            return None
        # 句界对齐：取最后一个句末符（找不到就整段切，避免流结束时丢尾）
        m = None
        for m in _SENT_END.finditer(self._buf):
            pass
        cut = (m.end() if m else len(self._buf))
        if cut < _CHUNK // 2 and m is None:
            return None  # 太短且无句界，继续攒
        chunk, self._buf = self._buf[:cut], self._buf[cut:]
        items = self._extract(chunk)
        if not items:
            # 这段没抽出新词也算一批（正文就 ~500 字，3 批覆盖全层）
            self._batches += 1
            return None
        self._batches += 1
        self._total += len(items)
        return items

    def _extract(self, text: str) -> list[dict]:
        """对一段正文抽新增候选：jieba TF-IDF + 相邻名词 bigram + 术语加分。

        bigram 补丁：jieba 对未登录复合词会切碎（'保留集是'->'保留'+'集是'、
        '数据子集'->'数据'+'子集'），TF-IDF 只能抽到碎片。相邻名词词元
        拼回 3-5 字复合词一并参与打分，按在正文中的实际出现次数给分。
        """
        try:
            import jieba
            import jieba.analyse as ja
        except ImportError:
            return []
        try:
            tags = ja.extract_tags(text, topK=10, withWeight=True,
                                   allowPOS=("n", "nr", "ns", "nt", "nz", "vn", "eng"))
        except Exception as e:  # noqa: BLE001
            log.debug("stream candidate extract failed: %s", e)
            return []
        # 相邻名词 bigram 合成候选（jieba 把未登录复合词切碎时的拼回通道）：
        # 两个词元都必须是名词类词性，否则会合成'定理指出'这类动词短语
        bigrams: dict[str, int] = {}
        try:
            import jieba.posseg as pseg
            words = [w for w in pseg.lcut(text) if w.word.strip()]
            for a, b in zip(words, words[1:]):
                if (a.flag in _NOUN_POS_SET and b.flag in _NOUN_POS_SET
                        and 2 <= len(a.word) <= 3 and 2 <= len(b.word) <= 3
                        and _ZH_TERM.fullmatch(a.word) and _ZH_TERM.fullmatch(b.word)
                        and not (_FUNC_CHARS & set(a.word))
                        and not (_FUNC_CHARS & set(b.word))):
                    bg = a.word + b.word
                    if 3 <= len(bg) <= 6 and bg.lower() not in _STOP_CONCEPTS:
                        bigrams[bg] = bigrams.get(bg, 0) + 1
        except Exception as e:  # noqa: BLE001
            log.debug("stream bigram failed: %s", e)

        # 英文连字符词拼回：'held'/'out'/'set' -> 'held-out set'。
        # jieba 把连字符词切碎成独立 eng 词元，碎片单独无意义
        for m in re.finditer(r"[A-Za-z][A-Za-z0-9]*(?:-[A-Za-z0-9]+)+\s+[A-Za-z][A-Za-z0-9]*", text):
            term = m.group(0)
            if term.lower() not in self._pushed:
                bigrams[term] = bigrams.get(term, 0) + 1
                # 碎片词元本身标记跳过（'held'/'out' 不再单独成候选）
                for tok in re.split(r"[\s-]+", term):
                    if len(tok) >= 2:
                        self._pushed.add(tok.lower())

        out: list[dict] = []
        seen_lc: set[str] = set()

        def _try_add(name: str, score: float) -> None:
            if len(out) >= 4 or name.lower() in seen_lc:
                return
            if (not name or len(name) < 2 or name.lower() in _STOP_CONCEPTS
                    or name.lower() in self._pushed):
                return
            # 功能字黑名单同样适用于 TF-IDF 通道：jieba 把'保留集是'切成
            # '保留'+'集是'，'集是'被标 n 词性照样进 TF-IDF。
            # 英文词不受此限（英文候选无功能字问题）
            if not _EN_TERM.search(name) and (_FUNC_CHARS & set(name)):
                return
            if score < 0.55:
                return
            seen_lc.add(name.lower())
            out.append({"name": name, "confidence": round(min(0.9, score * 0.5), 2)})

        # bigram 优先（复合词比碎片信息量大），再 TF-IDF top 词。
        # bigram 分数与 TF-IDF 同量级（避免压倒性挤掉英文术语），
        # 频次与术语形态学参与竞争
        for bg, freq in sorted(bigrams.items(), key=lambda x: -x[1]):
            _try_add(bg, 0.8 + 0.2 * freq + _term_boost(bg))
        for name, w in tags:
            _try_add(name, w + _term_boost(name))
        # 全部命中才标记已推送（避免部分失败的词被永久跳过）
        for item in out:
            self._pushed.add(item["name"].lower())
        return out
