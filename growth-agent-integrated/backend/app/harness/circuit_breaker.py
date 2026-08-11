"""熔断与重试策略（技术架构文档 7.3 / Harness 设计规格 §三）。

按模型端点维度统计错误率：
- 流式首 token 超时 5s 重试一次（幂等，正文未开始渲染）；
- 整体调用超时 60s 熔断进 L1；
- 结构化 JSON 超时 15s 按 L1 处理；
- 连续失败切备用模型/端点（failover）；
- 正文一旦开始流式渲染，任何失败只能降级、不能重试（正文不可撤回约束）。

状态机：closed（正常）→ open（熔断，直接失败不再调用）→ half-open（探活）→ closed。
"""
from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Optional

log = logging.getLogger(__name__)

FAILURE_THRESHOLD = 5          # 连续失败 N 次熔断
ERROR_RATE_THRESHOLD = 0.10    # 错误率 > 10%/h 告警
RECOVERY_PROBES = 1           # half-open 探活次数


class CircuitState(str, Enum):
    CLOSED = "closed"
    OPEN = "open"
    HALF_OPEN = "half_open"


@dataclass
class EndpointStats:
    endpoint: str
    failures: int = 0
    successes: int = 0
    consecutive_failures: int = 0
    state: CircuitState = CircuitState.CLOSED
    last_failure_at: float = 0.0
    opened_at: float = 0.0
    _recovery_attempts: int = 0

    @property
    def total(self) -> int:
        return self.failures + self.successes

    @property
    def error_rate(self) -> float:
        return self.failures / self.total if self.total else 0.0


class CircuitBreaker:
    """多端点熔断器。failover 链：primary -> backup -> ..."""

    def __init__(self, failover_chain: Optional[list[str]] = None,
                 failure_threshold: int = FAILURE_THRESHOLD,
                 cooldown_s: float = 30.0):
        self._chain = failover_chain or []
        self._threshold = failure_threshold
        self._cooldown = cooldown_s
        self._endpoints: dict[str, EndpointStats] = {}
        self._primary_idx = 0

    def stats(self, endpoint: str) -> EndpointStats:
        if endpoint not in self._endpoints:
            self._endpoints[endpoint] = EndpointStats(endpoint=endpoint)
        return self._endpoints[endpoint]

    def allow(self, endpoint: str) -> bool:
        """是否允许调用（熔断时拒绝）。half-open 放一个探活。"""
        s = self.stats(endpoint)
        if s.state == CircuitState.OPEN:
            if time.monotonic() - s.opened_at >= self._cooldown:
                s.state = CircuitState.HALF_OPEN
                s._recovery_attempts = 0
                log.info("breaker %s: open -> half_open", endpoint)
            else:
                return False
        if s.state == CircuitState.HALF_OPEN:
            if s._recovery_attempts >= RECOVERY_PROBES:
                return False
            s._recovery_attempts += 1
        return True

    def record_success(self, endpoint: str) -> None:
        s = self.stats(endpoint)
        s.successes += 1
        s.consecutive_failures = 0
        if s.state in (CircuitState.HALF_OPEN, CircuitState.OPEN):
            s.state = CircuitState.CLOSED
            log.info("breaker %s: -> closed (recovered)", endpoint)

    def record_failure(self, endpoint: str) -> None:
        s = self.stats(endpoint)
        s.failures += 1
        s.consecutive_failures += 1
        s.last_failure_at = time.monotonic()
        if s.consecutive_failures >= self._threshold and s.state != CircuitState.OPEN:
            s.state = CircuitState.OPEN
            s.opened_at = time.monotonic()
            log.warning("breaker %s: -> open (failures=%d, err_rate=%.2f)",
                        endpoint, s.failures, s.error_rate)

    def failover(self, current: str) -> Optional[str]:
        """当前端点熔断时，切到 failover 链下一个端点。"""
        try:
            idx = self._chain.index(current)
        except ValueError:
            idx = -1
        for nxt in self._chain[idx + 1:]:
            if self.allow(nxt):
                return nxt
        return None

    def snapshots(self) -> list[dict]:
        """端点维度熔断快照（/harness/obs/metrics 用）。"""
        return [{
            "endpoint": e, "state": s.state.value,
            "error_rate": round(s.error_rate, 4),
            "failures": s.failures, "successes": s.successes,
        } for e, s in self._endpoints.items()]

    @property
    def alert_triggered(self) -> bool:
        return any(s.error_rate > ERROR_RATE_THRESHOLD and s.total >= 10
                   for s in self._endpoints.values())
