"""Sentinel 检测器（协议设计文档 3.2）。

单次调用产出「流式正文 + 尾部 sentinel 分隔的结构化 JSON」：
模型先输出自然语言正文，以固定 sentinel（行首行尾锚定）分隔，后接 ConceptBlock JSON。

本模块实现「滑动窗口 + 前缀缓冲」检测，保证：
- 快路径：正常正文（不含 sentinel 前缀）无延迟吐出；
- 跨 chunk 切断：sentinel 无论在 chunk 边界任意位置切断都能正确检测，正文不泄漏 sentinel 字符；
- 缓冲上限 len(sentinel)-1（仅暂存可能是 sentinel 开头的末尾片段）。

与 inference/protocol.py 的 StreamSplitter 同源协议；本实现更贴合上游推理框架
规格（缓冲精确到 len(sentinel)-1，快路径一次判定），供真实 InferenceClient 使用。
"""
from __future__ import annotations

import json
import re
from typing import Optional

from app.config import settings
from app.schemas import ConceptBlock

SENTINEL = settings.concept_sentinel
# 行首行尾锚定，防正文误切分（技术架构文档 6.4）
_SENTINEL_RE = re.compile(rf"^\s*{re.escape(SENTINEL)}\s*$", re.MULTILINE)


class SentinelDetector:
    """把原始 token 流切成 正文增量 / sentinel / JSON buffer。

    feed() 喂入一段 token，返回 (answer_delta, sentinel_flag, json_block_or_None)。
    flush() 流结束时调用，吐出残留正文（无 sentinel 的降级情况）。
    """

    def __init__(self, sentinel: str = SENTINEL):
        self.sentinel = sentinel
        self._buf = ""           # 正文阶段暂存的末尾（可能是 sentinel 前缀）
        self._phase = "answer"   # answer -> json
        self._json_buf = ""
        self._sentinel_emitted = False

    @property
    def phase(self) -> str:
        return self._phase

    @property
    def sentinel_emitted(self) -> bool:
        return self._sentinel_emitted

    def feed(self, chunk: str):
        if self._phase == "answer":
            self._buf += chunk
            m = _SENTINEL_RE.search(self._buf)
            if m:
                answer = self._buf[: m.start()]
                rest = self._buf[m.end():]
                self._buf = ""
                self._phase = "json"
                self._sentinel_emitted = True
                self._json_buf = rest
                return answer, True, self._try_parse_json()
            # 无完整 sentinel：保留可能是 sentinel 前缀的末尾，其余安全吐出
            return self._safe_emit(), False, None
        # json 阶段
        self._json_buf += chunk
        return "", False, self._try_parse_json()

    def flush(self):
        """流结束：吐残留正文 / 尝试解析残留 JSON。"""
        if self._phase == "answer":
            out, self._buf = self._buf, ""
            return out, False, None
        return "", False, self._try_parse_json()

    def _safe_emit(self) -> str:
        """只吐确认非 sentinel 前缀的部分；末尾 len(sentinel)-1 字符暂存防泄漏。"""
        keep = max(len(self.sentinel) - 1, 0)
        if len(self._buf) <= keep:
            return ""
        emit_len = len(self._buf) - keep
        out = self._buf[:emit_len]
        self._buf = self._buf[emit_len:]
        return out

    def _try_parse_json(self) -> Optional[ConceptBlock]:
        if not self._json_buf:
            return None
        try:
            data = json.loads(self._json_buf)
            block = ConceptBlock(**data)
            self._json_buf = ""
            return block
        except (json.JSONDecodeError, ValueError, TypeError):
            return None


def split_stream(text: str, sentinel: str = SENTINEL):
    """一次性把完整文本切成 (answer, sentinel_flag, ConceptBlock|None)。

    供非流式抽取调用（L2 第二次 / 重试）使用。
    """
    det = SentinelDetector(sentinel)
    answer_parts: list[str] = []
    saw = False
    block: Optional[ConceptBlock] = None
    step = 8  # 任意非 1 步长即可，一次性文本只需走一遍检测逻辑
    for i in range(0, len(text), step):
        a, sent, blk = det.feed(text[i:i + step])
        if a:
            answer_parts.append(a)
        if sent:
            saw = True
        if blk is not None:
            block = blk
    a, sent, blk = det.flush()
    if a:
        answer_parts.append(a)
    if sent:
        saw = True
    if blk is not None:
        block = blk
    return "".join(answer_parts), saw, block
