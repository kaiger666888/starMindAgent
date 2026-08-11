"""熔断与重试测试：open/half-open/recover、failover、正文不可撤回。"""
import pytest
from app.harness.circuit_breaker import CircuitBreaker, CircuitState, FAILURE_THRESHOLD


def test_breaker_opens_after_threshold_failures():
    cb = CircuitBreaker(failure_threshold=3)
    for _ in range(3):
        cb.record_failure("ep-a")
    assert cb.stats("ep-a").state == CircuitState.OPEN
    assert cb.allow("ep-a") is False  # 熔断拒绝调用


def test_breaker_recovers_via_half_open():
    cb = CircuitBreaker(failure_threshold=2, cooldown_s=0.0)
    cb.record_failure("ep"); cb.record_failure("ep")
    assert cb.stats("ep").state == CircuitState.OPEN
    # cooldown=0 -> 立即 half_open，放一个探活
    assert cb.allow("ep") is True
    cb.record_success("ep")
    assert cb.stats("ep").state == CircuitState.CLOSED


def test_failover_to_backup_endpoint():
    cb = CircuitBreaker(failover_chain=["primary", "backup"])
    for _ in range(FAILURE_THRESHOLD):
        cb.record_failure("primary")
    assert cb.stats("primary").state == CircuitState.OPEN
    nxt = cb.failover("primary")
    assert nxt == "backup"


def test_success_resets_consecutive_failures():
    cb = CircuitBreaker(failure_threshold=3)
    cb.record_failure("ep"); cb.record_failure("ep")
    cb.record_success("ep")
    assert cb.stats("ep").consecutive_failures == 0
    assert cb.stats("ep").state == CircuitState.CLOSED


def test_snapshots_expose_per_endpoint():
    cb = CircuitBreaker()
    cb.record_failure("ep-a"); cb.record_success("ep-a")
    cb.record_failure("ep-b")
    snaps = cb.snapshots()
    eps = {s["endpoint"] for s in snaps}
    assert {"ep-a", "ep-b"} <= eps
    a = next(s for s in snaps if s["endpoint"] == "ep-a")
    assert a["failures"] == 1 and a["successes"] == 1
