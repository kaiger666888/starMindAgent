"""非功能指标可观测接口（设计规格 §六 / 架构文档 §9.4）。

暴露 FastAPI router：GET /harness/obs/metrics，返回四项核心指标 + 熔断快照，
门禁值内联返回，AI 评测工程师可直接对接。

指标来源：
- 中断恢复成功率 <- RecoveryCoordinator
- 熔断触发率（按端点）<- CircuitBreakerRegistry 快照
- 异步补标注完成率 / dead letter <- WorkerPool
- 回填 P95 延迟 <- WorkerPool 延迟采样
"""
from __future__ import annotations

from typing import Optional

from app.harness.recovery import RecoveryCoordinator
from app.harness.circuit_breaker import CircuitBreakerRegistry
from app.harness.reannotation import WorkerPool


class MetricsCollector:
    """聚合各模块指标，产出 /harness/obs/metrics 响应。"""

    def __init__(
        self,
        breaker_registry: CircuitBreakerRegistry,
        worker_pool: Optional[WorkerPool] = None,
        recovery: Optional[RecoveryCoordinator] = None,
    ):
        self.breaker_registry = breaker_registry
        self.worker_pool = worker_pool
        self.recovery = recovery
        # 中断恢复原始计数（recovery 未注入时兜底）
        self._recovery_attempts = 0
        self._recovery_successes = 0

    def record_recovery(self, kind: str, ok: bool) -> None:
        self._recovery_attempts += 1
        if ok:
            self._recovery_successes += 1

    def metrics(self) -> dict:
        # 中断恢复成功率
        if self.recovery is not None:
            rec = self.recovery.snapshot()
            success_rate = rec["success_rate"]
        else:
            success_rate = (
                self._recovery_successes / self._recovery_attempts
                if self._recovery_attempts else 1.0
            )
        # 熔断：按端点错误率
        snap = self.breaker_registry.snapshot()
        error_rate_per_endpoint = {
            ep: b["error_rate"] for ep, b in snap.items()
        }
        breaker_states = {ep: b["state"] for ep, b in snap.items()}

        # 异步补标注
        if self.worker_pool is not None:
            ws = self.worker_pool.stats()
            completion = ws["completion_rate"]
            dead_letter = ws["dead_letter_rate"]
            p95_ms = ws.get("p95_ms")
        else:
            completion, dead_letter, p95_ms = 1.0, 0.0, None

        return {
            "interruption_recovery": {
                "success_rate": round(success_rate, 4),
                "gate": "> 95%",
                "attempts": self._recovery_attempts,
            },
            "circuit_breaker": {
                "error_rate_per_endpoint": error_rate_per_endpoint,
                "states": breaker_states,
                "snapshots": snap,
                "alert_threshold": "> 10%/h",
            },
            "async_reannotation": {
                "completion_rate": round(completion, 4),
                "dead_letter_rate": round(dead_letter, 4),
                "gate": "completion > 90%, dead_letter < 2%",
            },
            "backfill_latency": {
                "p95_ms": p95_ms,
                "gate": "< 30000 ms",
            },
        }


def observability_router(metrics: MetricsCollector):
    """构建 GET /harness/obs/metrics router（挂载到 FastAPI app）。"""
    from fastapi import APIRouter
    router = APIRouter(prefix="/harness/obs", tags=["harness-observability"])

    @router.get("/metrics")
    async def get_metrics():
        return metrics.metrics()

    @router.get("/health")
    async def health():
        return {"status": "ok"}

    return router
