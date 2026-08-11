"""Harness 装配 + 集成 demo（设计规格 §八 / 主仓集成点）。

build_harness() 装配生产级 Harness 组件，返回可挂载到主仓 FastAPI app 的对象。
python -m app.harness.app 跑通 lifecycle demo（无需 DB / fastapi）。

主仓集成方式（routes_qa.py）：
    from app.harness import build_harness
    harness = build_harness(client=...)  # 注入推理框架工程师的真实 InferenceClient
    sess = await harness.manager.start(session_id, qa_id, question, model)
    pipe = QAStepPipeline(qa_id, session_id, question, sess, normalizer, repo)
    async for ev in pipe.run(): ...  # QAStepPipeline 直接消费 sess.stream()
"""
from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from typing import Optional

from app.harness.circuit_breaker import (
    BreakerConfig, CircuitBreakerRegistry, ResilientCaller, RetryPolicy,
)
from app.harness.inference_client import InferenceClient, StubInferenceClient
from app.harness.manager import InferenceSessionManager
from app.harness.models import HarnessTimeouts
from app.harness.observability import MetricsCollector, observability_router
from app.harness.reannotation import (
    InMemoryTaskStore, WorkerPool,
    make_default_backfill_handler, make_default_normalization_handler,
)
from app.harness.recovery import RecoveryCoordinator
from app.harness.store import InMemoryCheckpointStore

log = logging.getLogger(__name__)


@dataclass
class HarnessBundle:
    manager: InferenceSessionManager
    recovery: RecoveryCoordinator
    worker_pool: WorkerPool
    breaker_registry: CircuitBreakerRegistry
    metrics: MetricsCollector
    task_store: InMemoryTaskStore

    def routers(self):
        return [observability_router(self.metrics)]


def build_harness(
    client: Optional[InferenceClient] = None,
    *,
    failover_map: Optional[dict[str, list[str]]] = None,
    breaker_config: Optional[BreakerConfig] = None,
    timeouts: Optional[HarnessTimeouts] = None,
    worker_count: int = 2,
    normalizer=None,
    repo=None,
    concept_service=None,
    qa_exists_fn=None,
    on_refresh=None,
    checkpoint_store=None,
    task_store=None,
) -> HarnessBundle:
    """装配生产级 Harness。

    client：推理框架工程师注入的真实 InferenceClient（缺省用 StubInferenceClient）。
    normalizer / repo / concept_service：主仓依赖，缺省时 lazy 取 app.concept / app.qastep。
    qa_exists_fn：孤儿回收用的 QAStep 存在性校验，缺省走主仓 repo。
    """
    client = client or StubInferenceClient()
    timeouts = timeouts or HarnessTimeouts()
    breaker_registry = CircuitBreakerRegistry(breaker_config or BreakerConfig())
    caller = ResilientCaller(breaker_registry, failover_map)
    retry = RetryPolicy()

    checkpoint_store = checkpoint_store or InMemoryCheckpointStore()
    task_store = task_store or InMemoryTaskStore()

    # 主仓依赖 lazy 解析（避免 import 期硬耦合，便于单测注入桩）
    # 注意：app.concept.normalizer / app.qastep.repo 已是实例（见各 __init__.py），无需再取属性
    if normalizer is None:
        from app.concept import normalizer
        normalizer = normalizer
    if repo is None:
        from app.qastep import repo
        repo = repo
    if concept_service is None:
        from app.concept import concept_service
        concept_service = concept_service

    async def _qa_exists(qa_id: str) -> bool:
        if qa_exists_fn is not None:
            return await qa_exists_fn(qa_id)
        try:
            from sqlalchemy import select as sa_select
            from app.db import session_scope
            from app.models.tables import QAStep
            async with session_scope() as s:
                r = (await s.execute(
                    sa_select(QAStep.qa_id).where(QAStep.qa_id == qa_id)
                )).scalar_one_or_none()
                return r is not None
        except Exception:
            return False

    worker_pool = WorkerPool(
        store=task_store,
        backfill_handler=make_default_backfill_handler(client, normalizer, repo),
        normalization_handler=make_default_normalization_handler(concept_service),
        qa_exists_fn=_qa_exists,
        on_refresh=on_refresh,
        worker_count=worker_count,
    )

    metrics = MetricsCollector(
        breaker_registry=breaker_registry,
        worker_pool=worker_pool,
    )
    manager = InferenceSessionManager(
        client=client, caller=caller, retry=retry, timeouts=timeouts,
        checkpoint_store=checkpoint_store, reannotation_queue=worker_pool,
        on_metric=None,
    )
    recovery = RecoveryCoordinator(manager, observability=metrics)

    return HarnessBundle(
        manager=manager, recovery=recovery, worker_pool=worker_pool,
        breaker_registry=breaker_registry, metrics=metrics, task_store=task_store,
    )


async def _lifecycle_demo() -> None:
    """跑通 L0 主路径 demo（无需 DB / fastapi）。"""
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    bundle = build_harness()
    bundle.worker_pool.start()
    sess = await bundle.manager.start("sess-demo", "qa-demo", "什么是梯度下降", "demo-model")
    print("=== InferenceSession lifecycle (L0) ===")
    async for ev in sess.stream():
        print("event:", ev)
    print("state:", sess.state())
    await bundle.worker_pool.stop()


if __name__ == "__main__":
    asyncio.run(_lifecycle_demo())
