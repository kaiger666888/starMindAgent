"""熔断器 + 重试策略（设计规格 §4 / 架构文档 §7.3）。"""
import time

from app.harness.circuit_breaker import (
    BreakerConfig, BreakerState, CircuitBreaker, CircuitBreakerRegistry,
    ResilientCaller, RetryPolicy,
)
from app.harness.models import InferenceRequest


def test_consecutive_failures_trip_open():
    cfg = BreakerConfig(failure_threshold=3, min_samples_for_rate=99, recovery_seconds=30)
    b = CircuitBreaker("primary", cfg)
    for _ in range(3):
        b.record_failure()
    assert b.state == BreakerState.OPEN
    assert b.allow_call() is False


def test_error_rate_trip_open():
    cfg = BreakerConfig(failure_threshold=99, error_rate_threshold=0.5,
                        min_samples_for_rate=4, recovery_seconds=30)
    b = CircuitBreaker("primary", cfg)
    b.record_success(); b.record_success()
    b.record_failure(); b.record_failure()  # 4 样本，错误率 0.5
    assert b.state == BreakerState.OPEN


def test_half_open_recovery():
    cfg = BreakerConfig(failure_threshold=1, min_samples_for_rate=99, recovery_seconds=0.05)
    b = CircuitBreaker("primary", cfg)
    b.record_failure()
    assert b.state == BreakerState.OPEN
    time.sleep(0.06)
    assert b.allow_call() is True  # 转 HALF_OPEN 放一次探测
    assert b.state == BreakerState.HALF_OPEN
    b.record_success()
    assert b.state == BreakerState.CLOSED


def test_half_open_probe_failure_retrip():
    cfg = BreakerConfig(failure_threshold=1, min_samples_for_rate=99, recovery_seconds=0.05)
    b = CircuitBreaker("primary", cfg)
    b.record_failure()
    time.sleep(0.06)
    assert b.allow_call() is True
    b.record_failure()  # 探测失败
    assert b.state == BreakerState.OPEN


def test_resilient_caller_failover():
    reg = CircuitBreakerRegistry(BreakerConfig(failure_threshold=1, min_samples_for_rate=99))
    # 把 primary 打 OPEN
    reg.record_failure("primary")
    assert reg.get("primary").state == BreakerState.OPEN
    caller = ResilientCaller(reg, failover_map={"primary": ["backup"]})
    ep, forced = caller.select_endpoint(InferenceRequest(prompt="x", endpoint="primary"))
    assert ep == "backup"
    assert forced is False


def test_resilient_caller_all_open_fallback_primary():
    reg = CircuitBreakerRegistry(BreakerConfig(failure_threshold=1, min_samples_for_rate=99))
    reg.record_failure("primary")
    reg.record_failure("backup")
    caller = ResilientCaller(reg, failover_map={"primary": ["backup"]})
    ep, forced = caller.select_endpoint(InferenceRequest(prompt="x", endpoint="primary"))
    # 全部 OPEN -> 退回主端点拿真实错误
    assert ep == "primary"
    assert forced is True


def test_retry_policy_body_started_blocks_retry():
    rp = RetryPolicy(max_first_token_retry=1)
    assert rp.allow_first_token_retry(0, body_started=False) is True
    assert rp.allow_first_token_retry(1, body_started=False) is False  # 只重试一次
    assert rp.allow_first_token_retry(0, body_started=True) is False  # 正文已渲染不可重试


def test_registry_snapshot_per_endpoint():
    reg = CircuitBreakerRegistry()
    reg.record_success("primary")
    reg.record_failure("backup"); reg.record_failure("backup")
    snap = reg.snapshot()
    assert "primary" in snap and "backup" in snap
    assert snap["backup"]["consecutive_failures"] == 2
