"""Sentinel 检测 + JSON 累积 + 结构化解析（协议 §3.2 / §3.3 / §4.2）。

生产实现要点（优于参考实现 StreamSplitter）：
- 跨 chunk 边界匹配：滑动窗口 + sentinel 前缀缓冲，缓冲上限 len(sentinel)-1，
  保证延迟有界且不丢 token（协议 §3.2）。
- 命中后缓冲剩余直接转交 JSON 累积器。
- JSON 解析：约束解码路径流结束后一次解析；正则降级路径用最外层花括号配对
  定位边界（协议 §3.3 / §4.2 step1），兼容模型在 JSON 外加 markdown 围栏。
- 解析产物映射为主仓 app.schemas.ConceptBlock（QAStep 直接消费），字段映射：
  协议 canonical_name -> ConceptItem.name。
"""
from __future__ import annotations

import json
import re
from typing import Optional

from app.harness.models import SENTINEL, JsonState
from app.schemas import ConceptBlock, ConceptItem


class SentinelDetector:
    """跨 chunk sentinel 检测器（协议 §3.2 算法）。

    feed(chunk) -> (text_to_emit, sentinel_hit)
    - text_to_emit：可作为正文安全推送的文本
    - sentinel_hit：是否命中切分点（命中后进入 JSON 累积态）
    命中后剩余缓冲经 drain_json() 取出交给 JSON 累积器，不丢 token。
    """

    def __init__(self, sentinel: str = SENTINEL):
        self.sentinel = sentinel
        self.buf = ""
        self.matched = False
        self._max_keep = max(1, len(sentinel) - 1)

    def feed(self, chunk: str) -> tuple[str, bool]:
        if self.matched:
            # 命中后所有输入转交 JSON 累积（由调用方累积）
            self.buf += chunk
            return "", False
        if not chunk:
            return "", False
        self.buf += chunk
        # 缓冲中既无换行也无 ≡：不可能含 sentinel 前缀，全部发出
        if "\n" not in self.buf and "≡" not in self.buf:
            out, self.buf = self.buf, ""
            return out, False
        # 查找完整 sentinel
        idx = self.buf.find(self.sentinel)
        if idx != -1:
            self.matched = True
            out = self.buf[:idx]
            self.buf = self.buf[idx + len(self.sentinel):]  # 剩余交给 JSON 累积
            return out, True
        # 保守保留可能是 sentinel 前缀的尾部，其余发出（延迟有界）
        safe = self._safe_emit_length()
        out = self.buf[:safe]
        self.buf = self.buf[safe:]
        return out, False

    def drain_json(self) -> str:
        """命中 sentinel 后取出已缓冲的 JSON 起始片段。"""
        rest, self.buf = self.buf, ""
        return rest

    def flush_answer(self) -> str:
        """流结束且未命中 sentinel：吐出残留正文（L1 无结构化场景）。"""
        if self.matched:
            return ""
        out, self.buf = self.buf, ""
        return out

    @property
    def hit(self) -> bool:
        return self.matched

    def _safe_emit_length(self) -> int:
        """从缓冲尾部向前找最后一个可能构成 sentinel 前缀的位置。

        简化策略：保留末尾 _max_keep 个字符不吐（可能是 sentinel 开头）；
        但若保留区内已含换行+≡组合之外的完整内容则尽量放出，避免无谓延迟。
        """
        n = len(self.buf)
        if n <= self._max_keep:
            return 0
        # 保留末尾 _max_keep；但若保留区起点之后没有 sentinel 首字符，可全放
        keep_start = n - self._max_keep
        tail = self.buf[keep_start:]
        # sentinel 首字符为 '\n'；保留区无 '\n' 则不可能开始 sentinel，全放
        if "\n" not in tail and "≡" not in tail:
            return n
        return keep_start


class JsonAccumulator:
    """sentinel 之后的 JSON 累积器（协议 §3.3）。

    - 约束解码路径：流结束后一次解析
    - 正则降级路径：最外层花括号配对定位边界
    累积期间不向前端推送任何 token。
    """

    def __init__(self):
        self.buf = ""
        self.state: JsonState = JsonState.ACCUMULATING

    def feed(self, chunk: str) -> None:
        if self.state == JsonState.PARSED:
            return
        self.buf += chunk

    def try_parse(self, *, strict: bool = False) -> Optional[ConceptBlock]:
        """尝试解析。strict=True 仅在 buf 为完整 JSON 时解析（约束解码路径）。

        返回 ConceptBlock 或 None（未完整 / 解析失败）。
        """
        if self.state == JsonState.PARSED:
            return None
        candidate = _extract_json_block(self.buf)
        if candidate is None:
            if strict:
                return None
            # 非严格：花括号未闭合则视为未完整
            if not _looks_complete(self.buf):
                return None
            candidate = self.buf.strip()
        try:
            data = json.loads(candidate)
        except (json.JSONDecodeError, ValueError):
            return None
        block = _to_concept_block(data)
        if block is None:
            return None
        self.state = JsonState.PARSED
        return block

    def fail(self) -> None:
        self.state = JsonState.FAILED

    @property
    def raw(self) -> str:
        return self.buf


def _looks_complete(s: str) -> bool:
    """花括号是否配平闭合（粗判 JSON 边界，协议 §3.3 正则降级路径）。"""
    depth = 0
    in_str = False
    esc = False
    for ch in s:
        if in_str:
            if esc:
                esc = False
            elif ch == "\\":
                esc = True
            elif ch == '"':
                in_str = False
            continue
        if ch == '"':
            in_str = True
        elif ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
    return depth <= 0 and not in_str


def _extract_json_block(s: str) -> Optional[str]:
    """最外层花括号配对截取 JSON（协议 §4.2 step1，兼容 markdown 围栏）。"""
    start = s.find("{")
    if start == -1:
        return None
    depth = 0
    in_str = False
    esc = False
    for i in range(start, len(s)):
        ch = s[i]
        if in_str:
            if esc:
                esc = False
            elif ch == "\\":
                esc = True
            elif ch == '"':
                in_str = False
            continue
        if ch == '"':
            in_str = True
        elif ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                return s[start:i + 1]
    return None  # 未闭合


def _to_concept_block(data: object) -> Optional[ConceptBlock]:
    """协议 JSON schema -> 主仓 ConceptBlock（字段映射 canonical_name->name）。"""
    if not isinstance(data, dict):
        return None
    concepts = data.get("concepts")
    if not isinstance(concepts, list):
        return None
    items: list[ConceptItem] = []
    for c in concepts:
        if not isinstance(c, dict):
            continue
        name = c.get("canonical_name") or c.get("name")
        if not isinstance(name, str):
            continue
        items.append(ConceptItem(
            name=name,
            aliases=list(c.get("aliases", []) or []),
            confidence=float(c.get("confidence", 0.0) or 0.0),
            relation_type=c.get("relation_type", "related") or "related",
        ))
    return ConceptBlock(concepts=items)


# 行首行尾锚定正则（架构文档 §6.4 / 协议 §2.2），正文内嵌零散 ≡ 不触发
SENTINEL_LINE_RE = re.compile(rf"^\s*{re.escape(SENTINEL.strip())}\s*$", re.MULTILINE)
