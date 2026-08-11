"""/harness/obs/metrics 可观测接口测试：四项指标字段粒度 + 门禁值。"""
import pytest
from app.harness.observability import Observability, GATE_RECOVERY_SUCCESS_RATE
from app.harness.circuit_breaker import CircuitBreaker
from app.harness.store import InMemoryTaskStore
from app.harness.reannotation import WorkerPool


def test_metrics_fields_present():
    obs = Observability()
    obs.attach_breaker(CircuitBreaker())
    obs.attach_pool(WorkerPool(InMemoryTaskStore(), lambda q: asyncio.sleep(0)))
    m = obs.metrics()
    for key in ["interruption_recovery", "circuit_breaker",
                "async_reannotation", "backfill_latency", "timeouts"]:
        assert key in m
    # 中断恢复门禁内联
    assert m["interruption_recovery"]["gate"] == f">{GATE_RECOVERY_SUCCESS_RATE}"
    # 熔断快照 + 告警阈值
    assert isinstance(m["circuit_breaker"]["snapshots"], list)
    assert "alert_threshold_per_hour" in m["circuit_breaker"]
    # 补标注门禁
    assert ">" in m["async_reannotation"]["gate_completion"]
    assert "<" in m["async_reannotation"]["gate_dead_letter"]
    # 回填延迟门禁
    assert m["backfill_latency"]["gate"] == "<30000 ms"


import asyncio  # noqa: E402


def test_recovery_success_rate_tracking():
    obs = Observability()
    obs.record_recovery(True); obs.record_recovery(True); obs.record_recovery(False)
    m = obs.metrics()
    assert m["interruption_recovery"]["attempts"] == 3
    assert m["interruption_recovery"]["successes"] == 2
    assert abs(m["interruption_recovery"]["success_rate"] - 0.6667) < 0.01


def test_timeouts_aligned_to_settings():
    from app.config import settings
    obs = Observability()
    m = obs.metrics()
    assert m["timeouts"]["first_token_s"] == settings.first_token_timeout_s
    assert m["timeouts"]["overall_s"] == settings.inference_timeout_s
    assert m["timeouts"]["json_parse_s"] == settings.json_parse_timeout_s
