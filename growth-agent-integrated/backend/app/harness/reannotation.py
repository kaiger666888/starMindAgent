"""L1 异步补标注生命周期（技术架构文档 7.4 / Harness 设计规格 §四）。

「待补标注」落库为持久任务（qa_id + 状态 + 重试计数），后台 worker 池消费：
- 成功后回填 concept_ids 并触发前端增量刷新；
- 重试 3 次仍失败标 dead，前端静默保持无标注（不影响主流程）；
- 回填前校验目标 QAStep 存在性，孤儿任务直接回收；
- 归一化调用（merge/undo）强制串行化，防止与并发下钻乱序写。
"""
from __future__ import annotations

import asyncio
import logging
from typing import Awaitable, Callable, Optional

from app.config import settings
from app.harness.store import InMemoryTaskStore, TaskStore

log = logging.getLogger(__name__)


class ReannotationWorker:
    """后台补标注 worker：消费持久任务，串行归一化，重试上限 dead。"""

    def __init__(self, store: TaskStore,
                 processor: Callable[[str], Awaitable[None]],
                 qa_exists_fn: Callable[[str], Awaitable[bool]] | None = None,
                 on_complete: Callable[[str], Awaitable[None]] | None = None):
        self.store = store
        self.processor = processor
        self.qa_exists_fn = qa_exists_fn
        self.on_complete = on_complete
        self._tasks: list[asyncio.Task] = []
        self._stop = asyncio.Event()

    async def enqueue(self, qa_id: str) -> None:
        await self.store.enqueue(qa_id)

    def start(self, n_workers: int = 2) -> None:
        self._stop.clear()
        for i in range(n_workers):
            self._tasks.append(asyncio.create_task(self._run(i)))

    async def stop(self) -> None:
        self._stop.set()
        for t in self._tasks:
            t.cancel()
        self._tasks.clear()

    async def _run(self, wid: int) -> None:
        while not self._stop.is_set():
            pending = await self.store.pending()
            if not pending:
                await asyncio.sleep(0.05)
                continue
            for qa_id in pending:
                await self._process(qa_id)

    async def _process(self, qa_id: str) -> None:
        # 孤儿任务回收
        if self.qa_exists_fn is not None:
            try:
                exists = await self.qa_exists_fn(qa_id)
            except Exception:  # noqa: BLE001
                exists = True
            await self.store.recycle_orphan(qa_id, exists)
            if not exists:
                log.warning("orphan backfill recycled qa_id=%s", qa_id)
                return
        if not await self.store.claim(qa_id):
            return
        try:
            await self.processor(qa_id)
            await self.store.complete(qa_id)
            if self.on_complete:
                await self.on_complete(qa_id)
        except Exception as e:  # noqa: BLE001
            new_status = await self.store.fail(qa_id, str(e))
            log.warning("backfill failed qa_id=%s retry->%s err=%s",
                        qa_id, new_status, str(e)[:120])


class WorkerPool:
    """多 worker 池封装（带 backfill 延迟采样）。"""

    def __init__(self, store: TaskStore, processor, qa_exists_fn=None,
                 on_complete=None, n_workers: int = 2):
        self.worker = ReannotationWorker(store, processor, qa_exists_fn, on_complete)
        self.n_workers = n_workers
        self._latencies: list[float] = []  # 回填延迟样本（ms）
        self._lock = asyncio.Lock()

    def start(self) -> None:
        self.worker.start(self.n_workers)

    async def stop(self) -> None:
        await self.worker.stop()

    async def enqueue(self, qa_id: str) -> None:
        await self.worker.enqueue(qa_id)

    async def record_latency(self, ms: float) -> None:
        async with self._lock:
            self._latencies.append(ms)
            if len(self._latencies) > 1000:
                self._latencies = self._latencies[-1000:]

    def p95_latency_ms(self) -> float:
        if not self._latencies:
            return 0.0
        s = sorted(self._latencies)
        return s[int(len(s) * 0.95)] if len(s) > 1 else s[0]
