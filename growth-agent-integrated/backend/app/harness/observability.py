"""可观测接口（技术架构文档 9.4 / Harness 设计规格 §五）。

GET /harness/obs/metrics 暴露四项非功能指标 + 端点维度熔断快照，门禁值内联返回：
  中断恢复成功率     interruption_recovery.success_rate   门禁 > 95%
  熔断              circuit_breaker.snapshots (per endpoint) 告警 > 10%/h
  异步补标注        async_reannotation.completion_rate > 90%, dead_letter_rate < 2%
  回填延迟          backfill_latency.p95_ms                门禁 < 30000 ms
"""
from __future__ import annotations

from dataclasses import dataclass, field

from app.config import settings

# 门禁值（内联返回，AI 评测工程师直接对接）
GATE_RECOVERY_SUCCESS_RATE = 0.95
GATE_BACKFILL_COMPLETION_RATE = 0.90
GATE_DEAD_LETTER_RATE = 0.02
GATE_BACKFILL_P95_MS = 30000
ALERT_CIRCUIT_RATE_PER_HOUR = 0.10


@dataclass
class RecoveryStats:
    attempts: int = 0
    successes: int = 0

    @property
    def success_rate(self) -> float:
        return self.successes / self.attempts if self.attempts else 1.0


class Observability:
    """非功能指标采集与聚合。"""

    def __init__(self):
        self.recovery = RecoveryStats()
        self._breaker = None
        self._pool = None

    def attach_breaker(self, breaker) -> None:
        self._breaker = breaker

    def attach_pool(self, pool) -> None:
        self._pool = pool

    def record_recovery(self, success: bool) -> None:
        self.recovery.attempts += 1
        if success:
            self.recovery.successes += 1

    def metrics(self) -> dict:
        cb_snaps = self._breaker.snapshots() if self._breaker else []
        cb_alert = self._breaker.alert_triggered if self._breaker else False
        pool = self._pool
        if pool is not None:
            tstats = pool.worker.store
        else:
            tstats = None
        # 异步补标注统计
        completion_rate = 1.0
        dead_letter_rate = 0.0
        total = done = dead = 0
        if self._pool is not None:
            import asyncio
            stats = self._pool.worker.store
            # 内存 store 同步可读
            if hasattr(stats, "_tasks"):
                total = len(stats._tasks)
                done = sum(1 for t in stats._tasks.values() if t["status"] == "done")
                dead = sum(1 for t in stats._tasks.values() if t["status"] == "dead")
            completion_rate = done / total if total else 1.0
            dead_letter_rate = dead / total if total else 0.0
        p95 = self._pool.p95_latency_ms() if self._pool else 0.0
        return {
            "interruption_recovery": {
                "success_rate": round(self.recovery.success_rate, 4),
                "attempts": self.recovery.attempts,
                "successes": self.recovery.successes,
                "gate": f">{GATE_RECOVERY_SUCCESS_RATE}",
                "passed": self.recovery.success_rate > GATE_RECOVERY_SUCCESS_RATE,
            },
            "circuit_breaker": {
                "snapshots": cb_snaps,
                "alert_triggered": cb_alert,
                "alert_threshold_per_hour": ALERT_CIRCUIT_RATE_PER_HOUR,
            },
            "async_reannotation": {
                "completion_rate": round(completion_rate, 4),
                "dead_letter_rate": round(dead_letter_rate, 4),
                "total_tasks": total,
                "completed": done,
                "dead_letter": dead,
                "gate_completion": f">{GATE_BACKFILL_COMPLETION_RATE}",
                "gate_dead_letter": f"<{GATE_DEAD_LETTER_RATE}",
                "passed": (completion_rate > GATE_BACKFILL_COMPLETION_RATE
                           and dead_letter_rate < GATE_DEAD_LETTER_RATE),
            },
            "backfill_latency": {
                "p95_ms": round(p95, 2),
                "gate": f"<{GATE_BACKFILL_P95_MS} ms",
                "passed": p95 < GATE_BACKFILL_P95_MS,
            },
            "timeouts": {
                "first_token_s": settings.first_token_timeout_s,
                "overall_s": settings.inference_timeout_s,
                "json_parse_s": settings.json_parse_timeout_s,
            },
        }


observability = Observability()
