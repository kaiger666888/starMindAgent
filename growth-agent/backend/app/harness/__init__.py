"""Harness 生命周期层 —— 生产级实现（设计规格 §一 五个交付项）。

对外公共 API：
- build_harness(): 装配生产级 Harness，返回 HarnessBundle
- InferenceSession: 实现主仓 InferenceSession Protocol（QAStepPipeline 直接消费）
- InferenceSessionManager: start/abort/resume/get/state 统一 API
- RecoveryCoordinator: 中断恢复（用户回上层 / 网络断连）
- CircuitBreaker / CircuitBreakerRegistry / ResilientCaller / RetryPolicy: 熔断重试
- WorkerPool / InMemoryTaskStore / SqlTaskStore: 异步补标注 worker 池
- MetricsCollector / observability_router: GET /harness/obs/metrics
- InferenceClient Protocol / StubInferenceClient: 推理框架契约（注入点）

模块映射（设计规格 §一）：
  ① InferenceSession 抽象     -> inference_session.py + manager.py
  ② 流式中断状态恢复           -> recovery.py + store.py
  ③ 推理调用熔断与重试         -> circuit_breaker.py
  ④ L1 异步补标注生命周期      -> reannotation.py
  ⑤ 非功能指标可观测接口       -> observability.py
"""
from app.harness.models import (
    SENTINEL, Checkpoint, DegradeLevel, HarnessTimeouts, InferenceRequest,
    JsonState, SessionStatus, StreamChunk,
)
from app.harness.sentinel import SentinelDetector, JsonAccumulator
from app.harness.inference_client import InferenceClient, StubInferenceClient
from app.harness.circuit_breaker import (
    BreakerConfig, BreakerState, CircuitBreaker, CircuitBreakerRegistry,
    CircuitOpenError, ResilientCaller, RetryPolicy,
)
from app.harness.inference_session import InferenceSession
from app.harness.manager import InferenceSessionManager
from app.harness.recovery import RecoveryCoordinator
from app.harness.reannotation import (
    InMemoryTaskStore, ReannotationTask, SqlTaskStore, TaskKind, TaskStatus,
    WorkerPool, make_default_backfill_handler, make_default_normalization_handler,
)
from app.harness.store import CheckpointStore, InMemoryCheckpointStore, SqlCheckpointStore
from app.harness.observability import MetricsCollector, observability_router
from app.harness.app import HarnessBundle, build_harness

__all__ = [
    "SENTINEL", "Checkpoint", "DegradeLevel", "HarnessTimeouts", "InferenceRequest",
    "JsonState", "SessionStatus", "StreamChunk",
    "SentinelDetector", "JsonAccumulator",
    "InferenceClient", "StubInferenceClient",
    "BreakerConfig", "BreakerState", "CircuitBreaker", "CircuitBreakerRegistry",
    "CircuitOpenError", "ResilientCaller", "RetryPolicy",
    "InferenceSession", "InferenceSessionManager", "RecoveryCoordinator",
    "InMemoryTaskStore", "ReannotationTask", "SqlTaskStore", "TaskKind", "TaskStatus",
    "WorkerPool", "make_default_backfill_handler", "make_default_normalization_handler",
    "CheckpointStore", "InMemoryCheckpointStore", "SqlCheckpointStore",
    "MetricsCollector", "observability_router",
    "HarnessBundle", "build_harness",
]
