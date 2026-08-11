"""Sentinel 跨 chunk 检测器（协议设计文档 3.2）。

严格按协议设计文档 3.2 的 SentinelDetector 算法实现：
- sentinel = "\\n{core}\\n"（精确字符串，core = settings.concept_sentinel）；
- 滑动窗口 + sentinel 前缀缓冲：feed 时用 find(sentinel) 检测完整 sentinel，
  未命中时只发出「确认非 sentinel 前缀」的前缀，尾部保留可能是 sentinel 前缀的部分；
- 缓冲上限 len(sentinel)-1，保证延迟有界；
- 命中后剩余 token 直接转入 JSON 累积缓冲，不丢 token。

行首行尾正则锚定（协议 2.2 三重防护之一）仅在 flush() 兜底使用：流结束时若
sentinel 尾随换行缺失，用行级正则补判一次，避免 JSON 段被误当正文。
"""
from __future__ import annotations

import re
from typing import Tuple


def build_full_sentinel(core: str) -> str:
    """core 为 settings.concept_sentinel（不含换行），组装成带首尾换行的完整 sentinel。

    协议要求 sentinel 独占一行，首尾各加 \\n；正文中行首行尾锚定由「仅匹配
    \\n{core}\\n」语义保证，正文内嵌的零散 ≡ 不会触发。
    """
    return f"\n{core}\n"


class SentinelDetector:
    """跨 chunk sentinel 检测 + JSON 累积（协议 3.2）。

    feed(chunk) -> (text_to_emit, sentinel_hit)：
    - 命中前：text_to_emit 为可安全作为正文推送的文本；
    - 命中时：text_to_emit 为 sentinel 之前的正文尾巴，sentinel_hit=True，
      sentinel 之后的剩余文本转入内部 json 缓冲；
    - 命中后：text_to_emit 恒为空，chunk 累积进 json 缓冲。

    flush() 在流结束时调用，吐出残留正文；若残留中恰好出现 sentinel 也补判一次。
    """

    def __init__(self, sentinel: str):
        self.sentinel = sentinel  # 形如 "\n≡≡CONCEPT_BLOCK≡≡\n"
        self._core = sentinel.strip("\n")
        self._first = self._core[0]  # sentinel 的第一个非换行字符，用于快路径
        self._buf = ""
        self._json_buf = ""
        self._matched = False
        # flush 兜底：行级正则（允许 sentinel 行首尾无换行 / 仅空白），仅在流结束用
        self._flush_line_re = re.compile(
            rf"\n[ \t]*{re.escape(self._core)}[ \t]*(?=\n|$)|^[ \t]*{re.escape(self._core)}[ \t]*$",
            re.MULTILINE,
        )

    @property
    def matched(self) -> bool:
        return self._matched

    @property
    def json_text(self) -> str:
        """sentinel 命中后累积的 JSON 段文本。"""
        return self._json_buf

    def feed(self, chunk: str) -> Tuple[str, bool]:
        if self._matched:
            self._json_buf += chunk
            return "", False

        self._buf += chunk

        # 快路径：缓冲中既无换行也无 sentinel 首字符，绝无可能是 sentinel 前缀，全发出
        if "\n" not in self._buf and self._first not in self._buf:
            out, self._buf = self._buf, ""
            return out, False

        # 完整 sentinel 命中
        idx = self._buf.find(self.sentinel)
        if idx != -1:
            return self._commit(idx, len(self.sentinel))

        # 保守保留「是 sentinel 前缀」的最长尾部，其余作为正文发出
        safe = self._safe_emit_length()
        out = self._buf[:safe]
        self._buf = self._buf[safe:]
        return out, False

    def flush(self) -> Tuple[str, bool]:
        """流结束：吐出残留正文；残留中若出现完整 sentinel 或行级 sentinel 也补判。"""
        if self._matched:
            return "", False

        # 完整 sentinel
        idx = self._buf.find(self.sentinel)
        if idx != -1:
            return self._commit(idx, len(self.sentinel))

        # 行级兜底（流结束，无误判风险）：sentinel 行首尾换行缺失时补判
        m = self._flush_line_re.search(self._buf)
        if m is not None and m.group(0).strip() == self._core:
            return self._commit_match(m)

        out, self._buf = self._buf, ""
        return out, False

    # ----- internals -----
    def _commit(self, idx: int, sent_len: int) -> Tuple[str, bool]:
        """完整 sentinel 命中：out 为 sentinel 之前的正文（不含前导换行）。"""
        self._matched = True
        out = self._buf[:idx]
        self._json_buf = self._buf[idx + sent_len:]
        self._buf = ""
        return out, True

    def _commit_match(self, m: "re.Match") -> Tuple[str, bool]:
        """行级 sentinel 命中：out 不含 sentinel 行及其前导换行。"""
        self._matched = True
        start = m.start()
        out = self._buf[:start]
        # 若匹配从 \n 开始（含前导换行），out 已不含该换行；
        # 若匹配从行首 ^ 开始（无前导换行），out 本就不含。
        self._json_buf = self._buf[m.end():]
        # JSON 段开头的换行归 JSON（非正文）
        self._json_buf = self._json_buf.lstrip("\r\n")
        self._buf = ""
        return out, True

    def _safe_emit_length(self) -> int:
        """返回可安全发出的前缀长度；尾部保留「是 sentinel 前缀」的最长后缀。

        缓冲上限 len(sentinel)-1（不可能保留完整 sentinel，前面已 find 过）。
        """
        n = len(self._buf)
        s = self.sentinel
        cap = min(n, len(s) - 1)
        best = 0
        for i in range(1, cap + 1):
            if s.startswith(self._buf[n - i:]):
                best = i
        return n - best
