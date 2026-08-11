"""L1 异步补标注 worker 池（设计规格 §五 / 架构文档 §7.4 / 协议 §5.3）。

生产实现要点（替换参考实现的轻量 asyncio.Queue）：
- 待补标注落库为持久任务（qa_id + 状态 + 重试计数），后台 worker 池消费
- claim 原子化（内存用锁 / Sql 用 FOR UPDATE SKIP LOCKED），worker 重启从 DB 恢复
- 成功回填 concept_ids 并触发前端增量刷新（on_refresh 回调）
- 重试 3 次仍失败标 dead，前端静默保持无标注
- 回填前校验目标 QAStep 存在性，孤儿任务直接回收（reclaimed）
- 归一化（merge/undo）走同一任务通道（kind 区分），按 session 维度强制串行化

处理器与归一化执行器以依赖注入方式接入（默认接主仓 app.concept / app.qastep），
便于单测用桩替换，不依赖 DB。
"""
from __future__ import annotations

import asyncio
import logging
import uuid
from dataclasses import dataclass, field
from enum import Enum
from typing import Awaitable, Callable, Optional, Protocol

from app.config import settings

log = logging.getLogger(__name__)


class TaskKind(str, Enum):
    REANNOTATION = "reannotation"
    NORMALIZATION = "normalization"


class TaskStatus(str, Enum):
    PENDING = "pending"
    RUNNING = "running"
    DONE = "done"
    DEAD = "dead"
    RECLAIMED = "reclaimed"


@dataclass
class ReannotationTask:
    task_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    qa_id: str = ""
    session_id: str = ""
    kind: str = TaskKind.REANNOTATION.value
    status: str = TaskStatus.PENDING.value
    retry_count: int = 0
    payload: dict = field(default_factory=dict)  # reannotation: {answer_snapshot, reason}; normalization: merge/undo 参数
    last_error: Optional[str] = None
    created_at: float = 0.0
    updated_at: float = 0.0


# 处理器契约（依赖注入）
BackfillHandler = Callable[[ReannotationTask], Awaitable[dict]]
NormalizationHandler = Callable[[ReannotationTask], Awaitable[dict]]
QaExistsFn = Callable[[str], Awaitable[bool]]
RefreshFn = Callable[[str, list], Awaitable[None]]


class TaskStore(Protocol):
    async def enqueue(self, task: ReannotationTask) -> None: ...
    async def claim(self, kind: Optional[str] = None) -> Optional[ReannotationTask]: ...
    async def mark_done(self, task_id: str) -> None: ...
    async def mark_dead(self, task_id: str, error: str) -> None: ...
    async def requeue(self, task_id: str, error: str) -> None: ...  # retry+1 -> pending or dead
    async def reclaim(self, task_id: str, reason: str) -> None: ...  # 孤儿任务 -> reclaimed
    async def reclaim_orphans(self, exists_fn: QaExistsFn) -> int: ...
    async def reset_running(self) -> int: ...  # 启动: running->pending（stale 回收）
    async def stats(self) -> dict: ...


class InMemoryTaskStore:
    """进程内任务存储（单测用）。claim 用 asyncio.Lock 保证原子。"""

    def __init__(self, max_retry: Optional[int] = None):
        self._tasks: dict[str, ReannotationTask] = {}
        self._lock = asyncio.Lock()
        self._seq = 0.0
        # dead-letter 阈值单一来源：store 自身（默认读 settings，可注入便于单测）
        self.max_retry = max_retry if max_retry is not None else settings.backfill_max_retry

    async def enqueue(self, task: ReannotationTask) -> None:
        async with self._lock:
            if task.created_at == 0.0:
                self._seq += 1
                task.created_at = task.updated_at = self._seq
            self._tasks[task.task_id] = task

    async def claim(self, kind: Optional[str] = None) -> Optional[ReannotationTask]:
        async with self._lock:
            candidates = [t for t in self._tasks.values()
                          if t.status == TaskStatus.PENDING.value
                          and (kind is None or t.kind == kind)]
            if not candidates:
                return None
            candidates.sort(key=lambda t: t.created_at)
            t = candidates[0]
            t.status = TaskStatus.RUNNING.value
            self._seq += 1
            t.updated_at = self._seq
            return t

    async def mark_done(self, task_id: str) -> None:
        async with self._lock:
            t = self._tasks.get(task_id)
            if t:
                t.status = TaskStatus.DONE.value
                self._seq += 1
                t.updated_at = self._seq

    async def mark_dead(self, task_id: str, error: str) -> None:
        async with self._lock:
            t = self._tasks.get(task_id)
            if t:
                t.status = TaskStatus.DEAD.value
                t.last_error = error[:500]
                self._seq += 1
                t.updated_at = self._seq

    async def reclaim(self, task_id: str, reason: str) -> None:
        async with self._lock:
            t = self._tasks.get(task_id)
            if t:
                t.status = TaskStatus.RECLAIMED.value
                t.last_error = reason[:500]
                self._seq += 1
                t.updated_at = self._seq

    async def requeue(self, task_id: str, error: str) -> None:
        async with self._lock:
            t = self._tasks.get(task_id)
            if not t:
                return
            t.retry_count += 1
            t.last_error = error[:500]
            if t.retry_count >= self.max_retry:
                t.status = TaskStatus.DEAD.value
            else:
                t.status = TaskStatus.PENDING.value
            self._seq += 1
            t.updated_at = self._seq

    async def reclaim_orphans(self, exists_fn: QaExistsFn) -> int:
        async with self._lock:
            pending = [t for t in self._tasks.values()
                       if t.status in (TaskStatus.PENDING.value, TaskStatus.RUNNING.value)
                       and t.kind == TaskKind.REANNOTATION.value]
        reclaimed = 0
        for t in pending:
            if not await exists_fn(t.qa_id):
                async with self._lock:
                    t.status = TaskStatus.RECLAIMED.value
                    t.last_error = "orphan: qa_step deleted"
                    self._seq += 1
                    t.updated_at = self._seq
                reclaimed += 1
        return reclaimed

    async def reset_running(self) -> int:
        async with self._lock:
            n = 0
            for t in self._tasks.values():
                if t.status == TaskStatus.RUNNING.value:
                    t.status = TaskStatus.PENDING.value
                    n += 1
            return n

    async def stats(self) -> dict:
        async with self._lock:
            out: dict[str, int] = {}
            for t in self._tasks.values():
                out[t.status] = out.get(t.status, 0) + 1
            return out

    def all_tasks(self) -> list[ReannotationTask]:
        return list(self._tasks.values())


class WorkerPool:
    """补标注 / 归一化 worker 池（设计规格 §五）。"""

    def __init__(
        self,
        store: TaskStore,
        backfill_handler: BackfillHandler,
        normalization_handler: NormalizationHandler,
        qa_exists_fn: QaExistsFn,
        on_refresh: Optional[RefreshFn] = None,
        worker_count: int = 2,
        poll_interval: float = 0.05,
    ):
        self.store = store
        self.backfill_handler = backfill_handler
        self.normalization_handler = normalization_handler
        self.qa_exists_fn = qa_exists_fn
        self.on_refresh = on_refresh
        # dead-letter 阈值由 store 统一持有（见 store.max_retry）
        self.worker_count = worker_count
        self.poll_interval = poll_interval
        self._workers: list[asyncio.Task] = []
        self._stop = asyncio.Event()
        # 归一化按 session 维度串行化（架构文档 §3.2 / §7.4）
        self._norm_locks: dict[str, asyncio.Lock] = {}
        self._norm_guard = asyncio.Lock()
        # 延迟采样（回填 P95，设计规格 §六）
        self._latencies: list[float] = []
        self._completed = 0
        self._dead = 0

    # —— 入队 ——
    async def enqueue_reannotation(self, qa_id: str, session_id: str,
                                   answer_snapshot: str, reason: str = "L1") -> str:
        task = ReannotationTask(
            qa_id=qa_id, session_id=session_id, kind=TaskKind.REANNOTATION.value,
            payload={"answer_snapshot": answer_snapshot, "reason": reason},
        )
        await self.store.enqueue(task)
        return task.task_id

    async def enqueue_normalization(self, session_id: str, payload: dict,
                                    qa_id: str = "") -> str:
        task = ReannotationTask(
            qa_id=qa_id, session_id=session_id, kind=TaskKind.NORMALIZATION.value,
            payload=payload,
        )
        await self.store.enqueue(task)
        return task.task_id

    # —— 生命周期 ——
    def start(self) -> None:
        # 启动前回收 stale running 任务（worker 重启恢复）
        asyncio.create_task(self._bootstrap())
        for _ in range(self.worker_count):
            self._workers.append(asyncio.create_task(self._run()))

    async def _bootstrap(self) -> None:
        try:
            n = await self.store.reset_running()
            if n:
                log.info("reclaimed %d stale running tasks on startup", n)
            await self.store.reclaim_orphans(self.qa_exists_fn)
        except Exception:
            log.exception("worker bootstrap failed")

    async def stop(self) -> None:
        self._stop.set()
        for w in self._workers:
            w.cancel()
        await asyncio.gather(*self._workers, return_exceptions=True)
        self._workers.clear()

    async def _run(self) -> None:
        while not self._stop.is_set():
            try:
                task = await self.store.claim()
                if task is None:
                    await asyncio.sleep(self.poll_interval)
                    continue
                await self._process(task)
            except asyncio.CancelledError:
                break
            except Exception:
                log.exception("worker loop error")

    async def _process(self, task: ReannotationTask) -> None:
        start = asyncio.get_running_loop().time()
        try:
            if task.kind == TaskKind.REANNOTATION.value:
                # 孤儿校验：回填前校验目标 QAStep 存在性（设计规格 §五）
                if not await self.qa_exists_fn(task.qa_id):
                    await self.store.reclaim(task.task_id, "orphan: qa_step deleted")
                    log.warning("orphan reannotation task reclaimed qa_id=%s", task.qa_id)
                    return
                result = await self.backfill_handler(task)
                concept_ids = result.get("concept_ids", [])
                await self.store.mark_done(task.task_id)
                self._completed += 1
                # 成功回填 -> 触发前端增量刷新（设计规格 §五 step3）
                if self.on_refresh and concept_ids:
                    try:
                        await self.on_refresh(task.qa_id, concept_ids)
                    except Exception:
                        log.exception("on_refresh failed qa_id=%s", task.qa_id)
            else:  # NORMALIZATION：按 session 串行化
                lock = await self._get_norm_lock(task.session_id)
                async with lock:
                    await self.normalization_handler(task)
                await self.store.mark_done(task.task_id)
                self._completed += 1
            latency = asyncio.get_running_loop().time() - start
            self._latencies.append(latency)
            if len(self._latencies) > 1000:
                self._latencies = self._latencies[-1000:]
        except Exception as e:
            await self.store.requeue(task.task_id, str(e))
            # requeue 可能转 dead
            fresh = await self._peek(task.task_id)
            if fresh and fresh.status == TaskStatus.DEAD.value:
                self._dead += 1
            log.warning("reannotation task failed qa_id=%s retry=%s err=%s",
                        task.qa_id, task.retry_count, str(e)[:120])

    async def _get_norm_lock(self, session_id: str) -> asyncio.Lock:
        async with self._norm_guard:
            lock = self._norm_locks.get(session_id)
            if lock is None:
                lock = asyncio.Lock()
                self._norm_locks[session_id] = lock
            return lock

    async def _peek(self, task_id: str) -> Optional[ReannotationTask]:
        # InMemoryTaskStore 暴露 all_tasks；Sql 实现自行覆盖
        if hasattr(self.store, "all_tasks"):
            for t in self.store.all_tasks():
                if t.task_id == task_id:
                    return t
        return None

    # —— 指标（设计规格 §六）——
    def stats(self) -> dict:
        total = self._completed + self._dead
        completion = (self._completed / total) if total else 1.0
        dead_rate = (self._dead / total) if total else 0.0
        return {
            "completion_rate": round(completion, 4),
            "dead_letter_rate": round(dead_rate, 4),
            "completed": self._completed,
            "dead": self._dead,
            "p95_ms": self._p95_ms(),
        }

    def _p95_ms(self) -> Optional[int]:
        if not self._latencies:
            return None
        s = sorted(self._latencies)
        idx = max(0, int(len(s) * 0.95) - 1)
        return int(s[idx] * 1000)


# ---------------------------------------------------------------------------
# 默认处理器：接主仓 app.concept / app.qastep（lazy import，避免 import 期耦合）
# ---------------------------------------------------------------------------
def make_default_backfill_handler(client, normalizer, repo):
    """L1 补标注处理器：extract_only -> 归一化 -> 回填 concept_ids（架构文档 §7.4）。"""
    async def handler(task: ReannotationTask) -> dict:
        from sqlalchemy import update as sa_update
        from app.db import session_scope
        from app.models.tables import QAStep
        from app.schemas import ConceptItem

        answer = task.payload.get("answer_snapshot", "")
        block = await client.extract_only(answer, model="extract-only")
        resolved: list[dict] = []
        for item in block.concepts:
            # 膨胀超限则只标注已有（架构文档 §6.3 L3 回退语义）
            matched = await normalizer.match_existing_only(item.name, task.session_id)
            if matched:
                resolved.append(matched)
            else:
                resolved.append(await normalizer.normalize(item, task.qa_id, task.session_id))
        if resolved:
            await repo.link_co_occurrence(task.qa_id, task.session_id, resolved)
            async with session_scope() as s:
                await s.execute(
                    sa_update(QAStep).where(QAStep.qa_id == task.qa_id)
                    .values(extracted_concept_ids=[c["concept_id"] for c in resolved])
                )
        return {"concept_ids": [c["concept_id"] for c in resolved]}
    return handler


def make_default_normalization_handler(concept_service):
    """归一化（merge/undo）处理器：走 concept_service，已在 service 层全局串行。"""
    async def handler(task: ReannotationTask) -> dict:
        p = task.payload
        if p.get("action") == "merge":
            return await concept_service.merge_concepts(p["id_a"], p["id_b"], qa_id=task.qa_id or None)
        if p.get("action") == "undo":
            return await concept_service.undo_merge(p["merge_id"])
        raise ValueError(f"unknown normalization action: {p.get('action')}")
    return handler



# ---------------------------------------------------------------------------
# SqlTaskStore：生产用持久任务存储（Postgres，asyncpg）
# DDL 见 migrations/002_harness.sql：harness_task 表
# ---------------------------------------------------------------------------
class SqlTaskStore:
    """Postgres 持久任务存储。claim 用 FOR UPDATE SKIP LOCKED 保证多 worker 原子领取。"""

    TABLE = "harness_task"

    def __init__(self, session_scope, max_retry: Optional[int] = None):
        self._session_scope = session_scope
        self.max_retry = max_retry if max_retry is not None else settings.backfill_max_retry

    async def enqueue(self, task: ReannotationTask) -> None:
        import json
        from sqlalchemy import text
        sql = text(f"""
            INSERT INTO {self.TABLE}
              (task_id, qa_id, session_id, kind, status, retry_count, payload, last_error)
            VALUES (:tid,:qa,:sid,:kind,'pending',0,:payload,NULL)
            ON CONFLICT (task_id) DO NOTHING
        """)
        async with self._session_scope() as s:
            await s.execute(sql.bindparams(
                tid=task.task_id, qa=task.qa_id, sid=task.session_id,
                kind=task.kind, payload=json.dumps(task.payload),
            ))

    async def claim(self, kind: Optional[str] = None) -> Optional[ReannotationTask]:
        import json
        from sqlalchemy import text
        kind_filter = "AND kind = :kind" if kind else ""
        sql = text(f"""
            WITH next AS (
              SELECT task_id FROM {self.TABLE}
              WHERE status = 'pending' {kind_filter}
              ORDER BY created_at FOR UPDATE SKIP LOCKED LIMIT 1
            )
            UPDATE {self.TABLE} SET status='running', updated_at=now()
            FROM next WHERE {self.TABLE}.task_id = next.task_id
            RETURNING {self.TABLE}.task_id, qa_id, session_id, kind, status,
                      retry_count, payload, last_error
        """)
        async with self._session_scope() as s:
            row = (await s.execute(
                sql.bindparams(kind=kind) if kind else sql
            )).first()
        if row is None:
            return None
        m = row._mapping
        return ReannotationTask(
            task_id=m["task_id"], qa_id=m["qa_id"], session_id=m["session_id"],
            kind=m["kind"], status=m["status"], retry_count=m["retry_count"],
            payload=m["payload"] or {}, last_error=m["last_error"],
        )

    async def mark_done(self, task_id: str) -> None:
        from sqlalchemy import text
        async with self._session_scope() as s:
            await s.execute(text(
                f"UPDATE {self.TABLE} SET status='done', updated_at=now() WHERE task_id=:id"
            ).bindparams(id=task_id))

    async def mark_dead(self, task_id: str, error: str) -> None:
        from sqlalchemy import text
        async with self._session_scope() as s:
            await s.execute(text(
                f"UPDATE {self.TABLE} SET status='dead', last_error=:e, updated_at=now() WHERE task_id=:id"
            ).bindparams(id=task_id, e=error[:500]))

    async def reclaim(self, task_id: str, reason: str) -> None:
        from sqlalchemy import text
        async with self._session_scope() as s:
            await s.execute(text(
                f"UPDATE {self.TABLE} SET status='reclaimed', last_error=:e, updated_at=now() WHERE task_id=:id"
            ).bindparams(id=task_id, e=reason[:500]))

    async def requeue(self, task_id: str, error: str) -> None:
        from sqlalchemy import text
        async with self._session_scope() as s:
            await s.execute(text(f"""
                UPDATE {self.TABLE}
                SET retry_count = retry_count + 1,
                    status = CASE WHEN retry_count + 1 >= :mr THEN 'dead' ELSE 'pending' END,
                    last_error = :e, updated_at = now()
                WHERE task_id = :id
            """).bindparams(id=task_id, mr=self.max_retry, e=error[:500]))

    async def reclaim_orphans(self, exists_fn: QaExistsFn) -> int:
        from sqlalchemy import text
        async with self._session_scope() as s:
            rows = (await s.execute(text(
                f"SELECT task_id, qa_id FROM {self.TABLE} "
                f"WHERE status IN ('pending','running') AND kind='reannotation'"
            ))).all()
        n = 0
        for r in rows:
            if not await exists_fn(r._mapping["qa_id"]):
                async with self._session_scope() as s:
                    await s.execute(text(
                        f"UPDATE {self.TABLE} SET status='reclaimed', "
                        f"last_error='orphan: qa_step deleted', updated_at=now() WHERE task_id=:id"
                    ).bindparams(id=r._mapping["task_id"]))
                n += 1
        return n

    async def reset_running(self) -> int:
        from sqlalchemy import text
        async with self._session_scope() as s:
            res = await s.execute(text(
                f"UPDATE {self.TABLE} SET status='pending', updated_at=now() WHERE status='running'"
            ))
            return res.rowcount

    async def stats(self) -> dict:
        from sqlalchemy import text
        async with self._session_scope() as s:
            rows = (await s.execute(text(
                f"SELECT status, count(*) AS c FROM {self.TABLE} GROUP BY status"
            ))).all()
        return {r._mapping["status"]: r._mapping["c"] for r in rows}
