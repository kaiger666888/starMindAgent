"""
非功能指标门禁
==============

对齐 PRD 六 / 技术架构文档 9.4:
  - 流式中断恢复成功率 > 95%
  - 异步补标注完成率 > 90% (dead letter < 2%)
  - 回填 P95 延迟 < 30s
  - 熔断触发率告警阈值 > 10%/h

依赖: Harness 工程师暴露非功能指标可观测接口。
管线框架可先搭建，接口到位后接入跑通。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

from .models import NonFunctionalMetrics


@dataclass
class NonFunctionalGateResult:
    """非功能指标门禁判定结果"""
    # 各指标值
    streaming_recovery_success_rate: float = 0.0
    async_backfill_completion_rate: float = 0.0
    dead_letter_rate: float = 0.0
    backfill_p95_latency_ms: float = 0.0
    circuit_breaker_trigger_rate: float = 0.0
    # 门禁阈值
    GATE_STREAMING_RECOVERY_MIN: float = 0.95
    GATE_BACKFILL_COMPLETION_MIN: float = 0.90
    GATE_DEAD_LETTER_MAX: float = 0.02
    GATE_BACKFILL_P95_MAX_MS: float = 30_000   # 30s = 30000ms
    GATE_CIRCUIT_BREAKER_ALERT: float = 10.0    # > 10%/h 触发告警
    # 逐项通过状态
    checks: dict[str, dict] = field(default_factory=dict)
    # 总体
    all_passed: bool = False
    failed_gates: list[str] = field(default_factory=list)
    # 采样信息
    sample_count: int = 0
    time_window: str = ""


class NonFunctionalEvaluator:
    """非功能指标门禁评测器"""

    def evaluate(self, metrics: NonFunctionalMetrics) -> NonFunctionalGateResult:
        """
        评测非功能指标是否通过门禁。

        Args:
            metrics: 非功能指标快照

        Returns:
            NonFunctionalGateResult: 逐项门禁判定 + 总体结果
        """
        result = NonFunctionalGateResult()
        result.streaming_recovery_success_rate = metrics.streaming_recovery_success_rate
        result.async_backfill_completion_rate = metrics.async_backfill_completion_rate
        result.dead_letter_rate = metrics.dead_letter_rate
        result.backfill_p95_latency_ms = metrics.backfill_p95_latency_ms
        result.circuit_breaker_trigger_rate = metrics.circuit_breaker_trigger_rate
        result.sample_count = metrics.sample_count
        result.time_window = metrics.time_window

        failed = []

        # 1. 流式中断恢复成功率 > 95%
        passed_1 = metrics.streaming_recovery_success_rate > result.GATE_STREAMING_RECOVERY_MIN
        result.checks["streaming_recovery"] = {
            "value": metrics.streaming_recovery_success_rate,
            "threshold": f"> {result.GATE_STREAMING_RECOVERY_MIN}",
            "passed": passed_1,
            "description": "流式中断恢复成功率",
        }
        if not passed_1:
            failed.append("streaming_recovery")

        # 2. 异步补标注完成率 > 90%
        passed_2 = metrics.async_backfill_completion_rate > result.GATE_BACKFILL_COMPLETION_MIN
        result.checks["backfill_completion"] = {
            "value": metrics.async_backfill_completion_rate,
            "threshold": f"> {result.GATE_BACKFILL_COMPLETION_MIN}",
            "passed": passed_2,
            "description": "异步补标注完成率",
        }
        if not passed_2:
            failed.append("backfill_completion")

        # 3. dead letter < 2%
        passed_3 = metrics.dead_letter_rate < result.GATE_DEAD_LETTER_MAX
        result.checks["dead_letter"] = {
            "value": metrics.dead_letter_rate,
            "threshold": f"< {result.GATE_DEAD_LETTER_MAX}",
            "passed": passed_3,
            "description": "dead letter 比例",
        }
        if not passed_3:
            failed.append("dead_letter")

        # 4. 回填 P95 延迟 < 30s
        passed_4 = metrics.backfill_p95_latency_ms < result.GATE_BACKFILL_P95_MAX_MS
        result.checks["backfill_p95"] = {
            "value_ms": metrics.backfill_p95_latency_ms,
            "value_s": metrics.backfill_p95_latency_ms / 1000,
            "threshold": f"< {result.GATE_BACKFILL_P95_MAX_MS / 1000}s",
            "passed": passed_4,
            "description": "回填 P95 延迟",
        }
        if not passed_4:
            failed.append("backfill_p95")

        # 5. 熔断触发率告警 > 10%/h
        # 注意: 这是一个告警阈值，超过时需要排查，不是通过/不通过门禁
        alert_triggered = metrics.circuit_breaker_trigger_rate > result.GATE_CIRCUIT_BREAKER_ALERT
        result.checks["circuit_breaker"] = {
            "value_per_hour": metrics.circuit_breaker_trigger_rate,
            "threshold": f"> {result.GATE_CIRCUIT_BREAKER_ALERT}/h (告警)",
            "alert_triggered": alert_triggered,
            "description": "熔断触发率",
        }
        # 熔断是告警项，不纳入 all_passed 判定
        if alert_triggered:
            failed.append("circuit_breaker_alert")

        result.failed_gates = failed
        # 熔断告警不阻塞 all_passed（它是告警不是硬门禁）
        hard_failures = [f for f in failed if f != "circuit_breaker_alert"]
        result.all_passed = len(hard_failures) == 0

        return result
