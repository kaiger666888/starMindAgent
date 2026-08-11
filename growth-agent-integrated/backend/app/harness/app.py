"""Harness 装配入口：build_harness() + lifecycle demo + metrics handler。

build_harness(client=None):
  - 推理框架工程师实现 stream/abort/extract_only 三方法契约（见 inference_client.py），
    经 build_harness(client=real_client) 注入；缺省用 StubLLMBackend 跑通编排。
  - 装配：SessionManager + CircuitBreaker + ReannotationWorker + Observability。
  - 集成无侵入：InferenceSession 实现主仓已有 Protocol，QAStepPipeline 无需改动。

运行 demo：python3 -m app.harness.app
"""
from __future__ import annotations

import asyncio
import logging
from typing import Optional

from app.inference.backend import LLMBackend, StubLLMBackend, default_backend
from app.harness.manager import SessionManager
from app.harness.store import InMemoryCheckpointStore, InMemoryTaskStore
from app.harness.recovery import RecoveryManager
from app.harness.circuit_breaker import CircuitBreaker
from app.harness.reannotation import WorkerPool
from app.harness.observability import observability

log = logging.getLogger(__name__)

_harness = None


class Harness:
    def __init__(self, backend: LLMBackend | None = None,
                 breaker: CircuitBreaker | None = None,
                 checkpoint_store=None, task_store=None, pool: WorkerPool | None = None):
        self.backend = backend or default_backend()
        self.breaker = breaker or CircuitBreaker(
            failover_chain=[self.backend.endpoint])
        self.checkpoint_store = checkpoint_store or InMemoryCheckpointStore()
        self.recovery = RecoveryManager(self.checkpoint_store)
        self.task_store = task_store or InMemoryTaskStore()
        self.pool = pool
        self.manager = SessionManager(
            backend=self.backend, checkpoint_store=self.checkpoint_store,
            breaker=self.breaker, recovery=self.recovery,
            backfill_hook=self._backfill_hook)
        observability.attach_breaker(self.breaker)
        if self.pool:
            observability.attach_pool(self.pool)

    async def _backfill_hook(self, qa_id: str) -> None:
        """L1 触发：落库待补标注 + 启动 worker。"""
        await self.task_store.enqueue(qa_id)
        if self.pool and not self.pool.worker._tasks:
            self.pool.start()

    def session_for(self, qa_id: str, session_id: str, question: str):
        return self.manager.session_for(qa_id, session_id, question)

    def metrics(self) -> dict:
        return observability.metrics()


def build_harness(client: Optional[LLMBackend] = None,
                  pool: Optional[WorkerPool] = None) -> Harness:
    """装配 harness。client 缺省按环境配置（LLM_BASE_URL 配了用真实后端，否则 stub）。"""
    global _harness
    backend = client or default_backend()
    if pool is None:
        pool = WorkerPool(InMemoryTaskStore(), _default_processor,
                          qa_exists_fn=_qa_exists)
    _harness = Harness(backend=backend, pool=pool)
    return _harness


def get_harness() -> Harness:
    global _harness
    if _harness is None:
        _harness = build_harness()
    return _harness


# —— 默认补标注处理器：复用主仓 backfill_processor 逻辑 ——
async def _default_processor(qa_id: str) -> None:
    from app.inference.tasks import backfill_processor
    await backfill_processor(qa_id)


async def _qa_exists(qa_id: str) -> bool:
    try:
        from sqlalchemy import select
        from app.db import session_scope
        from app.models.tables import QAStep
        async with session_scope() as s:
            r = (await s.execute(select(QAStep.qa_id).where(QAStep.qa_id == qa_id))).scalar_one_or_none()
            return r is not None
    except Exception:  # noqa: BLE001  DB 不可用时保守不回收
        return True


async def _lifecycle_demo():
    """跑通 L0 全链路：InferenceSession stream 产出 ['delta','sentinel','json_done']。"""
    h = build_harness()
    sess = h.session_for("demo-qa", "demo-session", "什么是梯度下降？")
    events = [ev["kind"] async for ev in sess.stream()]
    print("event sequence:", events)
    # 多次 delta + sentinel + json_done
    assert events[0] == "delta", events
    assert "sentinel" in events and events[-1] == "json_done", events
    print("L0 lifecycle OK")


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    asyncio.run(_lifecycle_demo())
