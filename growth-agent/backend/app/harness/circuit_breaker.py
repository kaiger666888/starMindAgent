"""推理调用熔断与重试（设计规格 §4 / 架构文档 §7.3 / 协议 §7.3）。

三层能力：
1. CircuitBreaker：按模型端点维度统计错误率，连续失败 N 次或窗口错误率超阈值
   即 OPEN；OPEN 时按 failover_map 切备用端点；全部不可用退回主端点拿真实错误。
2. RetryPolicy：正文不可撤回约束——重试仅在「正文尚未推送」窗口内允许；正文一旦
   开始流式渲染，任何失败只能降级、不能重试（架构文档 §7.3 硬约束）。
3. ResilientCaller：在熔断器保护下打开推理流，选择端点（主 + 备用 failover）。

分层超时（首 token 5s / 整体 60s / JSON 15s）在 InferenceSession 中施加，
本模块只负责端点选择与熔断统计，职责分离便于单测。
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Optional

from app.harness.models import InferenceRequest


class BreakerState(str, Enum):
    CLOSED = "closed"
    OPEN = "open"
    HALF_OPEN = "half_open"


class CircuitOpenError(Exception):
    """所有候选端点熔断 OPEN（理论上 ResilientCaller 会退回主端点，极少抛出）。"""


@dataclass
class BreakerConfig:
    failure_threshold: int = 5          # 连续失败 N 次熔断
    error_rate_threshold: float = 0.5   # 窗口错误率阈值
    min_samples_for_rate: int = 5       # 错误率统计最小样本数
    recovery_seconds: float = 30.0      # OPEN -> HALF_OPEN 冷却
    window_size: int = 20               # 滑动窗口样本数


@dataclass
class _Window:
    samples: list = field(default_factory=list)  # [(success:bool, ts)]

    def record(self, success: bool) -> None:
        self.samples.append((success, time.monotonic()))
        if len(self.samples) > 200:
            self.samples = self.samples[-200:]

    def error_rate(self, min_samples: int) -> float:
        if len(self.samples) < min_samples:
            return 0.0
        fails = sum(1 for ok, _ in self.samples if not ok)
        return fails / len(self.samples)

    def consecutive_failures(self) -> int:
        n = 0
        for ok, _ in reversed(self.samples):
            if not ok:
                n += 1
            else:
                break
        return n


class CircuitBreaker:
    """单端点熔断器（设计规格 §4.2）。"""

    def __init__(self, endpoint: str, config: Optional[BreakerConfig] = None):
        self.endpoint = endpoint
        self.config = config or BreakerConfig()
        self.state = BreakerState.CLOSED
        self.opened_at: Optional[float] = None
        self._win = _Window()

    def allow_call(self) -> bool:
        """是否允许调用（CLOSED/HALF_OPEN 允许，OPEN 冷却到期转 HALF_OPEN 放一次探测）。"""
        if self.state == BreakerState.CLOSED:
            return True
        if self.state == BreakerState.OPEN:
            if self.opened_at and (time.monotonic() - self.opened_at) >= self.config.recovery_seconds:
                self.state = BreakerState.HALF_OPEN
                return True  # 放一次探测
            return False
        # HALF_OPEN：只允许一次探测，由 record 决定后续
        return True

    def record_success(self) -> None:
        self._win.record(True)
        if self.state == BreakerState.HALF_OPEN:
            self._reset()

    def record_failure(self) -> None:
        self._win.record(False)
        if self.state == BreakerState.HALF_OPEN:
            self._trip()
            return
        if self._win.consecutive_failures() >= self.config.failure_threshold:
            self._trip()
        elif self._win.error_rate(self.config.min_samples_for_rate) >= self.config.error_rate_threshold:
            self._trip()

    def _trip(self) -> None:
        self.state = BreakerState.OPEN
        self.opened_at = time.monotonic()

    def _reset(self) -> None:
        self.state = BreakerState.CLOSED
        self.opened_at = None

    def snapshot(self) -> dict:
        return {
            "endpoint": self.endpoint,
            "state": self.state.value,
            "error_rate": round(self._win.error_rate(self.config.min_samples_for_rate), 4),
            "samples": len(self._win.samples),
            "consecutive_failures": self._win.consecutive_failures(),
        }


class CircuitBreakerRegistry:
    """端点维度熔断器注册表（设计规格 §4.2「按模型端点维度统计」）。"""

    def __init__(self, config: Optional[BreakerConfig] = None):
        self.config = config or BreakerConfig()
        self._breakers: dict[str, CircuitBreaker] = {}

    def get(self, endpoint: str) -> CircuitBreaker:
        b = self._breakers.get(endpoint)
        if b is None:
            b = CircuitBreaker(endpoint, self.config)
            self._breakers[endpoint] = b
        return b

    def allow_call(self, endpoint: str) -> bool:
        return self.get(endpoint).allow_call()

    def record_success(self, endpoint: str) -> None:
        self.get(endpoint).record_success()

    def record_failure(self, endpoint: str) -> None:
        self.get(endpoint).record_failure()

    def snapshot(self) -> dict:
        """按端点维度的错误率快照（供 /harness/obs/metrics）。"""
        return {ep: b.snapshot() for ep, b in self._breakers.items()}


class RetryPolicy:
    """重试窗口判定（架构文档 §7.3 正文不可撤回约束）。

    - 首 token 超时：正文尚未渲染，幂等重试一次
    - 正文已开始流式渲染：任何失败只降级不重试
    - 结构化部分失败：不重试整次调用（走 extract_only 结构化重试 / L1 降级）
    """

    def __init__(self, max_first_token_retry: int = 1):
        self.max_first_token_retry = max_first_token_retry

    def allow_first_token_retry(self, attempt: int, body_started: bool) -> bool:
        if body_started:
            return False
        return attempt < self.max_first_token_retry


class ResilientCaller:
    """在熔断器保护下选择端点并打开推理流（设计规格 §4.2 failover）。

    端点选择顺序：主端点 + failover_map[主端点]；取第一个 allow_call 的端点；
    全部 OPEN 则退回主端点（让调用方拿到真实错误，设计规格 §4.2）。
    """

    def __init__(self, registry: CircuitBreakerRegistry,
                 failover_map: Optional[dict[str, list[str]]] = None):
        self.registry = registry
        self.failover_map = failover_map or {}

    def select_endpoint(self, req: InferenceRequest) -> tuple[str, bool]:
        """返回 (endpoint, forced)。forced=True 表示所有候选 OPEN 退回主端点。"""
        candidates = [req.endpoint] + list(self.failover_map.get(req.endpoint, []))
        for ep in candidates:
            if self.registry.allow_call(ep):
                return ep, False
        # 全部不可用：退回主端点拿真实错误
        return req.endpoint, True

    def record_success(self, endpoint: str) -> None:
        self.registry.record_success(endpoint)

    def record_failure(self, endpoint: str) -> None:
        self.registry.record_failure(endpoint)
