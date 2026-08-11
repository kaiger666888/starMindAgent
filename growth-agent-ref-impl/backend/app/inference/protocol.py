"""推理框架层协议与封装。

技术架构文档第六、七节：
- 输出协议：单次调用流式正文 + 尾部结构化 JSON（sentinel 分割）
- InferenceSession 封装流式读取 + sentinel 检测 + 降级判定，
  让 QAStep 状态机只关心业务语义、不感知推理层细节。
- 三级降级链路 L0/L1/L2/L3。

本模块定义 QAStep 依赖的协议边界（Protocol）+ 一个可独立运行的桩实现，
真实 InferenceSession 由 Harness 工程师提供并替换。
"""
from __future__ import annotations
import json
import logging
import re
from typing import AsyncIterator, Protocol, Optional

from app.config import settings
from app.schemas import ConceptBlock, ConceptItem

log = logging.getLogger(__name__)

SENTINEL = settings.concept_sentinel
# 行首行尾锚定，防正文误切分（技术架构文档 6.4）
_SENTINEL_RE = re.compile(rf"^\s*{re.escape(SENTINEL)}\s*$", re.MULTILINE)


class InferenceEvent(dict):
    """推理框架产出的事件，QAStep 消费。"""


class InferenceSession(Protocol):
    """Harness 工程师提供的推理会话封装。

    QAStep 只消费 stream() 的语义事件，不感知 sentinel / JSON 累积 / 降级细节。
    """
    session_id: str
    qa_id: str

    async def stream(self) -> AsyncIterator[dict]:
        """产出事件序列：
          {kind:'delta', text}      正文增量
          {kind:'sentinel'}          sentinel 已切分，进入 JSON 累积
          {kind:'json_done', block}  尾部 JSON 解析完成（ConceptBlock）
          {kind:'error', message}    失败（正文已流出则只降级不重试）
        """
        ...


# ---------------------------------------------------------------------------
# Sentinel 检测 + JSON 累积器（InferenceSession 内部用）
# ---------------------------------------------------------------------------
class StreamSplitter:
    """把原始 token 流切成 正文增量 / sentinel / JSON buffer。

    逐 token 检测：当 buffer 末尾出现 sentinel 行时切分，之后切到 JSON 累积模式。
    JSON 完整后一次性解析为 ConceptBlock（约束解码保证格式稳定）。
    """

    def __init__(self, sentinel: str = SENTINEL):
        self.sentinel = sentinel
        self._buf = ""
        self._phase = "answer"  # answer -> json
        self._json_buf = ""
        self._sentinel_emitted = False

    def feed(self, chunk: str):
        """喂入一段 token，返回 (answer_deltas, sentinel_flag, json_block_or_None)。"""
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
                block = self._try_parse_json()
                return answer, True, block
            # 无 sentinel：可能 sentinel 跨 chunk，保守保留末尾可能的前缀
            safe = self._safe_emit()
            return safe, False, None
        # json 阶段
        self._json_buf += chunk
        block = self._try_parse_json()
        return "", False, block

    def _safe_emit(self) -> str:
        """避免把 sentinel 前缀当正文吐出：只在确认非 sentinel 前缀时吐出。"""
        # 保留末尾 len(sentinel) 字符不吐（可能是 sentinel 的开头）
        keep = len(self.sentinel) + 2  # 含换行
        if len(self._buf) <= keep:
            return ""
        emit_len = len(self._buf) - keep
        out = self._buf[:emit_len]
        self._buf = self._buf[emit_len:]
        return out

    def flush(self):
        """流结束时调用，吐出残留正文（无 sentinel 的降级情况）。"""
        if self._phase == "answer":
            out = self._buf
            self._buf = ""
            return out, False, None
        block = self._try_parse_json()
        return "", False, block

    def _try_parse_json(self) -> Optional[ConceptBlock]:
        try:
            data = json.loads(self._json_buf)
            block = ConceptBlock(**data)
            self._json_buf = ""
            return block
        except (json.JSONDecodeError, ValueError):
            return None


# ---------------------------------------------------------------------------
# 桩 InferenceSession（本地开发 / 测试用）
# 模拟「流式正文 + sentinel + 尾部 JSON」协议，真实实现替换为推理框架接入。
# ---------------------------------------------------------------------------
class StubInferenceSession:
    """模拟一次成功的 L0 调用：先逐段吐正文，再吐 sentinel + ConceptBlock。"""

    def __init__(self, qa_id: str, session_id: str, question: str,
                 answer_chunks: list[str] | None = None,
                 concept_block: ConceptBlock | None = None):
        self.qa_id = qa_id
        self.session_id = session_id
        self.question = question
        self._answer_chunks = answer_chunks or [
            f"关于「{question}」，", "这是一个概念性主题。", "其核心在于……"
        ]
        self._concept_block = concept_block or ConceptBlock(
            concepts=[ConceptItem(name="概念A", aliases=["concept_a"], confidence=0.9)],
            model="stub-llm",
        )

    async def stream(self) -> AsyncIterator[dict]:
        for chunk in self._answer_chunks:
            yield {"kind": "delta", "text": chunk}
        yield {"kind": "sentinel"}
        yield {"kind": "json_done", "block": self._concept_block}


class FailingInferenceSession:
    """模拟 L1 降级：正文流出后 JSON 解析失败。"""
    def __init__(self, qa_id, session_id, question):
        self.qa_id, self.session_id, self.question = qa_id, session_id, question

    async def stream(self):
        yield {"kind": "delta", "text": "部分正文已渲染……"}
        yield {"kind": "sentinel"}
        # 不产出 json_done -> QAStep 走 L1：丢弃结构化部分，只返回正文
        yield {"kind": "error", "message": "json parse failed (L1)"}
