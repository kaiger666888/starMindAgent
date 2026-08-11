"""Harness 生命周期层数据模型。

对齐：
- Harness 设计规格文档 §2.1（InferenceSession 状态字段）
- 技术架构文档 §7.1（InferenceSession 抽象）
- 流式+结构化输出协议 §2.2 / §3（sentinel 单一来源、StreamChunk、事件）

设计原则：纯数据 + 枚举，不依赖 DB / 推理框架，便于单测离线运行。
sentinel 单一来源：以 app.config.settings.concept_sentinel 为基，按协议 §2.2
包成「独占一行」形态（前后换行），与主仓 StreamSplitter 行首行尾锚定一致。
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from enum import Enum
from typing import Optional


def _sentinel_base() -> str:
    """sentinel 基串：优先用主仓 settings（单一来源），回退环境变量。"""
    try:
        from app.config import settings  # 主仓已有
        return settings.concept_sentinel
    except Exception:  # pragma: no cover - 主仓未集成时回退
        return os.getenv("CONCEPT_SENTINEL", "≡≡CONCEPT_BLOCK≡≡")


# 协议 §2.2：SENTINEL = "\n≡≡CONCEPT_BLOCK≡≡\n"，独占一行才切分
SENTINEL = "\n" + _sentinel_base() + "\n"


class SessionStatus(str, Enum):
    """InferenceSession 生命周期状态（设计规格 §2.1）。"""
    STREAMING = "streaming"
    INTERRUPTED = "interrupted"
    COMPLETED = "completed"


class JsonState(str, Enum):
    """结构化 JSON 累积状态（协议 §3.1 流式状态机）。"""
    IDLE = "idle"
    ACCUMULATING = "accumulating"
    PARSED = "parsed"
    FAILED = "failed"


class DegradeLevel(str, Enum):
    """L0-L3 降级级别（架构文档 §6.3 / 协议 §5.2）。"""
    L0 = "L0"
    L1 = "L1"
    L2 = "L2"
    L3 = "L3"


@dataclass
class StreamChunk:
    """推理框架层产出的原始 token 块（协议 §2.3 / §3）。

    Harness 据此做 sentinel 切分与 JSON 累积，推理框架只吐原始流。
    call_id 供 abort（用户回上层时向推理层发取消）。
    """
    call_id: str
    delta: str = ""
    finish_reason: Optional[str] = None  # "stop" | "length" | None


@dataclass
class InferenceRequest:
    """一次推理调用请求。

    endpoint 是熔断器维度 key（设计规格 §4.2「按模型端点维度」）。
    resume_offset 用于断连续推：跳过已落盘的正文 prefix（协议 §7.2 推理调用不重启）。
    """
    prompt: str
    endpoint: str = "primary"
    model: str = "default"
    temperature: float = 0.0
    resume_offset: int = 0


@dataclass
class HarnessTimeouts:
    """分层超时（设计规格 §4.1 / 协议 §7.3，与推理框架对齐）。"""
    first_token_s: float = 5.0    # 流式首 token 超时 -> 重试一次
    overall_s: float = 60.0       # 整体调用超时 -> 熔断进 L1
    json_s: float = 15.0          # 结构化 JSON 超时 -> 按 L1 处理


@dataclass
class Checkpoint:
    """每个 QAStep 的流式中断恢复 checkpoint（设计规格 §2.1 / §三）。

    恢复协议以 qa_id + offset 为准（协议 §7.2）：前端重连丢弃 last-event-id
    之后的本地未确认内容，按重放事件对齐。
    """
    session_id: str
    qa_id: str
    answer_checkpoint: str = ""          # 已产出正文（不含 sentinel / JSON）
    sentinel_position: int = -1          # sentinel 检测位置，-1 未遇到
    json_state: str = JsonState.IDLE.value
    status: str = SessionStatus.STREAMING.value
    degrade_level: str = DegradeLevel.L0.value
    offset: int = 0                      # len(answer_checkpoint)，恢复协议基准
    last_event_id: int = 0               # SSE last-event-id 续推基准
    concept_ids: list = field(default_factory=list)
    call_id: Optional[str] = None
    endpoint: Optional[str] = None
    raw_json: str = ""                   # 已累积 JSON 片段（L1 落盘供异步补标注复用）

    def snapshot(self) -> dict:
        return {
            "session_id": self.session_id,
            "qa_id": self.qa_id,
            "answer_checkpoint": self.answer_checkpoint,
            "sentinel_position": self.sentinel_position,
            "json_state": self.json_state,
            "status": self.status,
            "degrade_level": self.degrade_level,
            "offset": self.offset,
            "last_event_id": self.last_event_id,
            "concept_ids": list(self.concept_ids),
            "endpoint": self.endpoint,
        }
