"""Harness 数据模型：checkpoint 与任务状态。"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Optional


class SessionStatus(str, Enum):
    STREAMING = "streaming"
    INTERRUPTED = "interrupted"
    COMPLETED = "completed"


class JsonState(str, Enum):
    ACCUMULATING = "accumulating"
    PARSED = "parsed"
    FAILED = "failed"


@dataclass
class Checkpoint:
    """单个 QAStep 的推理会话 checkpoint（架构文档 7.1）。"""
    qa_id: str
    session_id: str
    answer_checkpoint: str = ""          # 已产出正文
    sentinel_position: int = -1         # sentinel 检测位置；-1 未检测
    json_state: JsonState = JsonState.ACCUMULATING
    status: SessionStatus = SessionStatus.STREAMING
    concept_block_raw: Optional[str] = None  # 已累积的 JSON 文本
    last_event_id: int = 0              # SSE 续推用的最近事件序号
    model: str = "unknown"
    updated_at: datetime = field(default_factory=datetime.utcnow)


@dataclass
class ResumeResult:
    """resume() 统一返回结构（成功 / 未知 qa 一致）。"""
    qa_id: str
    checkpoint: Optional[Checkpoint]
    status: str   # "resumed" | "unknown"
