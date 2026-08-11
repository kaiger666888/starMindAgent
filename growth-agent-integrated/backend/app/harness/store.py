"""Checkpoint store + 补标注任务 store（内存 / Sql 双实现）。

- CheckpointStore：每个 QAStep 维护 session_id+qa_id+已产出正文 checkpoint+sentinel 位置+
  结构化 JSON 状态位；网络断开时 SSE 凭 last-event-id 重连从最近 checkpoint 续推。
- TaskStore：待补标注落库为持久任务（qa_id+状态+重试计数），worker 重启从 DB 恢复 pending。

Sql 实现复用主仓 AsyncSession（dev 用 aiosqlite，prod 用 asyncpg）。
max_retry 下沉到 store（可经构造注入），避免 frozen settings monkeypatch 问题。
"""
from __future__ import annotations

import logging
from typing import Optional

from sqlalchemy import select, update, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.harness.models import Checkpoint, JsonState, SessionStatus, ResumeResult

log = logging.getLogger(__name__)


class CheckpointStore:
    """Checkpoint store 契约（内存/Sql 实现均满足）。子类实现 save/get/resume。"""
    pass


class InMemoryCheckpointStore:
    """进程内 checkpoint store（测试 / 单实例部署用）。"""

    def __init__(self):
        self._data: dict[str, Checkpoint] = {}

    async def save(self, cp: Checkpoint) -> None:
        self._data[cp.qa_id] = cp

    async def get(self, qa_id: str) -> Optional[Checkpoint]:
        return self._data.get(qa_id)

    async def resume(self, qa_id: str) -> ResumeResult:
        cp = self._data.get(qa_id)
        if cp is None:
            return ResumeResult(qa_id=qa_id, checkpoint=None, status="unknown")
        # 续推：状态保持 streaming/中断点
        return ResumeResult(qa_id=qa_id, checkpoint=cp, status="resumed")

    async def clear(self, qa_id: str) -> None:
        self._data.pop(qa_id, None)


class SqlCheckpointStore:
    """Sql 持久化 checkpoint（网络断开后跨进程重连续推）。"""

    def __init__(self, session_factory):
        self._sf = session_factory

    async def save(self, cp: Checkpoint) -> None:
        async with self._sf() as s:  # type: AsyncSession
            await s.execute(text("""
                INSERT INTO harness_checkpoint
                    (qa_id, session_id, answer_checkpoint, sentinel_position,
                     json_state, status, last_event_id, model, updated_at)
                VALUES (:qa,:sid,:ans,:sent,:js,:st,:lei,:m, now())
                ON CONFLICT (qa_id) DO UPDATE SET
                    answer_checkpoint=EXCLUDED.answer_checkpoint,
                    sentinel_position=EXCLUDED.sentinel_position,
                    json_state=EXCLUDED.json_state,
                    status=EXCLUDED.status,
                    last_event_id=EXCLUDED.last_event_id,
                    model=EXCLUDED.model,
                    updated_at=now()
            """), {"qa": cp.qa_id, "sid": cp.session_id, "ans": cp.answer_checkpoint,
                "sent": cp.sentinel_position, "js": cp.json_state.value,
                "st": cp.status.value, "lei": cp.last_event_id, "m": cp.model})
            await s.commit()

    async def get(self, qa_id: str) -> Optional[Checkpoint]:
        async with self._sf() as s:
            row = (await s.execute(text(
                "SELECT * FROM harness_checkpoint WHERE qa_id=:q"), {"q": qa_id})).first()
        if row is None:
            return None
        m = row._mapping
        return Checkpoint(
            qa_id=m.qa_id, session_id=m.session_id,
            answer_checkpoint=m.answer_checkpoint,
            sentinel_position=m.sentinel_position,
            json_state=JsonState(m.json_state), status=SessionStatus(m.status),
            last_event_id=m.last_event_id, model=m.model,
        )

    async def resume(self, qa_id: str) -> ResumeResult:
        cp = await self.get(qa_id)
        if cp is None:
            return ResumeResult(qa_id=qa_id, checkpoint=None, status="unknown")
        return ResumeResult(qa_id=qa_id, checkpoint=cp, status="resumed")


# ---------------------------------------------------------------------------
# 补标注任务 store
# ---------------------------------------------------------------------------
class TaskStore:
    """持久化补标注任务（claim / 重试 / dead / 孤儿回收）。"""

    def __init__(self, max_retry: int | None = None):
        self.max_retry = max_retry if max_retry is not None else settings.backfill_max_retry


class InMemoryTaskStore(TaskStore):
    """内存任务表（测试用）。"""

    def __init__(self, max_retry: int | None = None):
        super().__init__(max_retry)
        self._tasks: dict[str, dict] = {}

    async def enqueue(self, qa_id: str) -> None:
        if qa_id in self._tasks:
            self._tasks[qa_id]["status"] = "pending"
            return
        self._tasks[qa_id] = {"qa_id": qa_id, "status": "pending",
                              "retry_count": 0, "last_error": None}

    async def claim(self, qa_id: str) -> bool:
        t = self._tasks.get(qa_id)
        if not t or t["status"] != "pending":
            return False
        t["status"] = "running"
        return True

    async def complete(self, qa_id: str) -> None:
        if qa_id in self._tasks:
            self._tasks[qa_id]["status"] = "done"

    async def fail(self, qa_id: str, err: str) -> str:
        """失败：retry_count+1；达上限标 dead，否则回 pending 重试。返回新状态。"""
        t = self._tasks.get(qa_id)
        if not t:
            return "dead"
        t["retry_count"] += 1
        t["last_error"] = err[:500]
        if t["retry_count"] >= self.max_retry:
            t["status"] = "dead"
        else:
            t["status"] = "pending"
        return t["status"]

    async def recycle_orphan(self, qa_id: str, qa_exists: bool) -> None:
        """孤儿任务回收：目标 QAStep 已删 -> 直接 dead。"""
        if not qa_exists and qa_id in self._tasks:
            self._tasks[qa_id]["status"] = "dead"
            self._tasks[qa_id]["last_error"] = "orphan: qa_step deleted"

    async def pending(self) -> list[str]:
        return [qa for qa, t in self._tasks.items() if t["status"] == "pending"]

    async def stats(self) -> dict:
        from collections import Counter
        c = Counter(t["status"] for t in self._tasks.values())
        return {"total": sum(c.values()), "done": c.get("done", 0),
                "dead": c.get("dead", 0), "pending": c.get("pending", 0),
                "running": c.get("running", 0)}


class SqlTaskStore(TaskStore):
    """Sql 持久化任务表（复用主仓 backfill_task）。"""

    def __init__(self, scope, max_retry: int | None = None):
        super().__init__(max_retry)
        self._scope = scope  # session_factory

    async def enqueue(self, qa_id: str) -> None:
        async with self._scope() as s:
            s.add(__import__("app.models.tables", fromlist=["BackfillTask"]).BackfillTask(
                qa_id=qa_id, status="pending"))

    async def claim(self, qa_id: str) -> bool:
        async with self._scope() as s:
            from app.models.tables import BackfillTask
            t = (await s.execute(select(BackfillTask).where(
                BackfillTask.qa_id == qa_id, BackfillTask.status == "pending")
                .order_by(BackfillTask.created_at).limit(1))).scalar_one_or_none()
            if not t:
                return False
            t.status = "running"
            await s.commit()
            return True

    async def complete(self, qa_id: str) -> None:
        async with self._scope() as s:
            from app.models.tables import BackfillTask
            await s.execute(update(BackfillTask).where(
                BackfillTask.qa_id == qa_id).values(status="done"))

    async def fail(self, qa_id: str, err: str) -> str:
        async with self._scope() as s:
            from app.models.tables import BackfillTask
            t = (await s.execute(select(BackfillTask).where(
                BackfillTask.qa_id == qa_id).order_by(
                BackfillTask.created_at.desc()).limit(1))).scalar_one()
            t.retry_count += 1
            t.last_error = err[:500]
            if t.retry_count >= self.max_retry:
                t.status = "dead"
            else:
                t.status = "pending"
            await s.commit()
            return t.status

    async def recycle_orphan(self, qa_id: str, qa_exists: bool) -> None:
        if qa_exists:
            return
        async with self._scope() as s:
            from app.models.tables import BackfillTask
            await s.execute(update(BackfillTask).where(
                BackfillTask.qa_id == qa_id, BackfillTask.status == "pending"
            ).values(status="dead", last_error="orphan: qa_step deleted"))

    async def stats(self) -> dict:
        async with self._scope() as s:
            from app.models.tables import BackfillTask
            from sqlalchemy import func
            rows = (await s.execute(
                select(BackfillTask.status, func.count()).group_by(BackfillTask.status))).all()
            d = {r[0]: r[1] for r in rows}
            return {"total": sum(d.values()), "done": d.get("done", 0),
                    "dead": d.get("dead", 0), "pending": d.get("pending", 0),
                    "running": d.get("running", 0)}
