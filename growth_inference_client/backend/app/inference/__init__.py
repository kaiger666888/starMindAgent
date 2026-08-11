"""推理框架层：协议桩 + 真实 InferenceClient 实现。

保留原有 protocol / tasks 导出，追加真实实现（client / sentinel / constraints /
context / backend），供 Agent 研发注入替换 StubInferenceSession。
"""
# 原有桩与任务（保持向后兼容）
from app.inference.protocol import (
    InferenceSession, StreamSplitter, StubInferenceSession, FailingInferenceSession,
    SENTINEL,
)
from app.inference.tasks import backfill_queue, backfill_processor

# 真实实现（替换 StubInferenceSession）
from app.inference.sentinel import SentinelDetector, build_full_sentinel
from app.inference.constraints import (
    ConstrainedDecoder,
    keyword_fallback,
    CONCEPT_BLOCK_SCHEMA,
)
from app.inference.context import ContextBudget, LayerSummary
from app.inference.backend import LLMBackend, OpenAICompatibleBackend, FakeLLMBackend
from app.inference.client import InferenceClient, FULL_SENTINEL

__all__ = [
    # 协议桩
    "InferenceSession", "StreamSplitter",
    "StubInferenceSession", "FailingInferenceSession", "SENTINEL",
    "backfill_queue", "backfill_processor",
    # 真实实现
    "InferenceClient",
    "SentinelDetector",
    "build_full_sentinel",
    "ConstrainedDecoder",
    "keyword_fallback",
    "CONCEPT_BLOCK_SCHEMA",
    "ContextBudget",
    "LayerSummary",
    "LLMBackend",
    "OpenAICompatibleBackend",
    "FakeLLMBackend",
    "FULL_SENTINEL",
]
