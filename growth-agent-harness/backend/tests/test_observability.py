"""/harness/obs/metrics 可观测接口（设计规格 §六 / 架构文档 §9.4）。"""
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.harness.circuit_breaker import CircuitBreakerRegistry
from app.harness.observability import MetricsCollector, observability_router
from app.harness.reannotation import InMemoryTaskStore, WorkerPool
from app.harness.recovery import RecoveryCoordinator
from app.harness.manager import InferenceSessionManager
from app.harness.circuit_breaker import ResilientCaller, RetryPolicy
from app.harness.store import InMemoryCheckpointStore
from app.harness.inference_client import StubInferenceClient


def _bundle():
    reg = CircuitBreakerRegistry()
    store = InMemoryTaskStore()

    async def backfill(task):
        return {"concept_ids": []}

    async def norm(task):
        return {}

    async def qa_exists(qa_id):
        return True

    pool = WorkerPool(store=store, backfill_handler=backfill, normalization_handler=norm,
                      qa_exists_fn=qa_exists)
    mgr = InferenceSessionManager(
        client=StubInferenceClient(), caller=ResilientCaller(reg), retry=RetryPolicy(),
        checkpoint_store=InMemoryCheckpointStore(), reannotation_queue=pool,
    )
    rec = RecoveryCoordinator(mgr)
    metrics = MetricsCollector(breaker_registry=reg, worker_pool=pool, recovery=rec)
    return metrics, reg, pool, rec


def test_metrics_fields_and_gates():
    metrics, reg, pool, rec = _bundle()
    # 注入一些熔断与恢复样本
    reg.record_success("primary")
    reg.record_failure("backup"); reg.record_failure("backup")
    rec._attempts = 10; rec._successes = 9
    m = metrics.metrics()
    assert set(m.keys()) == {"interruption_recovery", "circuit_breaker",
                             "async_reannotation", "backfill_latency"}
    assert m["interruption_recovery"]["gate"] == "> 95%"
    assert m["interruption_recovery"]["success_rate"] == 0.9
    assert "primary" in m["circuit_breaker"]["error_rate_per_endpoint"]
    assert m["circuit_breaker"]["alert_threshold"] == "> 10%/h"
    assert m["async_reannotation"]["gate"] == "completion > 90%, dead_letter < 2%"
    assert m["backfill_latency"]["gate"] == "< 30000 ms"


def test_metrics_router_serves_endpoint():
    metrics, reg, pool, rec = _bundle()
    app = FastAPI()
    app.include_router(observability_router(metrics))
    client = TestClient(app)
    r = client.get("/harness/obs/metrics")
    assert r.status_code == 200
    body = r.json()
    assert body["interruption_recovery"]["gate"] == "> 95%"
    assert "error_rate_per_endpoint" in body["circuit_breaker"]
    r2 = client.get("/harness/obs/health")
    assert r2.status_code == 200


def test_breaker_snapshot_granularity_per_endpoint():
    """熔断按端点维度上报（设计规格 §4.2 / §六）。"""
    metrics, reg, pool, rec = _bundle()
    reg.record_failure("primary"); reg.record_failure("primary")
    reg.record_success("backup")
    m = metrics.metrics()
    per_ep = m["circuit_breaker"]["error_rate_per_endpoint"]
    states = m["circuit_breaker"]["states"]
    assert "primary" in per_ep and "backup" in per_ep
    assert states["primary"] == "closed"
    assert "snapshots" in m["circuit_breaker"]
    assert m["circuit_breaker"]["snapshots"]["primary"]["consecutive_failures"] == 2
