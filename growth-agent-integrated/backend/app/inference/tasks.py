"""异步任务队列 + 异步补标注 worker。

技术架构文档 7.4：
- 「待补标注」落库为持久任务（qa_id + 状态 + 重试计数），由 harness 后台 worker 池消费
- 成功后回填 concept_ids 并触发前端增量刷新
- 重试 3 次仍失败则标记 dead，前端静默保持无标注
- 回填前校验目标 QAStep 存在性，孤儿任务直接回收

本模块用 asyncio.Queue 做进程内轻量队列 + DB 持久化兜底；
生产可替换为 Celery / RQ / DB 轮询 worker。
"""
from __future__ import annotations
import asyncio
import logging
from typing import Optional

from sqlalchemy import select, update

from app.db import session_scope
from app.models.tables import BackfillTask, QAStep
from app.config import settings

log = logging.getLogger(__name__)


class BackfillQueue:
    """进程内轻量任务队列（DB 持久化兜底，worker 重启后从 DB 恢复 pending）。"""

    def __init__(self):
        self._q: asyncio.Queue[str] = asyncio.Queue()
        self._worker_task: Optional[asyncio.Task] = None

    async def enqueue(self, qa_id: str) -> None:
        """落库 + 入队。"""
        async with session_scope() as s:
            s.add(BackfillTask(qa_id=qa_id, status="pending"))
        await self._q.put(qa_id)

    def start_worker(self, processor) -> None:
        if self._worker_task and not self._worker_task.done():
            return
        self._worker_task = asyncio.create_task(self._run(processor))

    async def _run(self, processor) -> None:
        while True:
            qa_id = await self._q.get()
            try:
                await self._process_one(processor, qa_id)
            except Exception as e:
                log.exception("backfill worker error qa_id=%s", qa_id)
            finally:
                self._q.task_done()

    async def _process_one(self, processor, qa_id: str) -> None:
        async with session_scope() as s:
            # 回填前校验目标 QAStep 存在性，孤儿任务直接回收
            exists = (
                await s.execute(select(QAStep.qa_id).where(QAStep.qa_id == qa_id))
            ).scalar_one_or_none()
            task = (
                await s.execute(
                    select(BackfillTask).where(
                        BackfillTask.qa_id == qa_id, BackfillTask.status == "pending"
                    ).order_by(BackfillTask.created_at).limit(1)
                )
            ).scalar_one_or_none()
            if not exists:
                if task:
                    task.status = "dead"
                    task.last_error = "orphan: qa_step deleted"
                log.warning("orphan backfill task for qa_id=%s, recycled", qa_id)
                return
            if task:
                task.status = "running"

        try:
            await processor(qa_id)
            async with session_scope() as s:
                await s.execute(
                    update(BackfillTask).where(BackfillTask.qa_id == qa_id).values(status="done")
                )
        except Exception as e:
            async with session_scope() as s:
                t = (
                    await s.execute(select(BackfillTask).where(BackfillTask.qa_id == qa_id).order_by(BackfillTask.created_at.desc()).limit(1))
                ).scalar_one()
                t.retry_count += 1
                t.last_error = str(e)[:500]
                if t.retry_count >= settings.backfill_max_retry:
                    t.status = "dead"  # dead letter < 2% 门禁
                else:
                    t.status = "pending"
                    await self._q.put(qa_id)  # 重新入队重试


backfill_queue = BackfillQueue()


async def backfill_processor(qa_id: str) -> None:
    """默认补标注处理器：用独立抽取调用补全概念。

    真实实现接推理框架的轻量抽取调用（技术架构文档 L1/L2）。
    这里用桩：直接调用 normalizer 对 raw_output 做关键词匹配（L3 回退路径）。
    """
    from app.concept import normalizer
    from app.models.tables import QAStep as Q
    from app.qastep.repository import repo
    from app.schemas import ConceptItem

    async with session_scope() as s:
        row = (await s.execute(select(Q).where(Q.qa_id == qa_id))).scalar_one_or_none()
        if not row or not row.raw_output:
            return
        session_id = row.session_id
        raw = row.raw_output

    # L3 回退：从预置概念表做关键词子串匹配（精度低但不空）
    # 这里简化：复用 normalizer.match_existing_only 对 raw 里的候选词
    # 真实应调用独立抽取模型
    candidates = _keyword_extract(raw)
    resolved = []
    for name in candidates:
        hit = await normalizer.match_existing_only(name, session_id)
        if hit:
            resolved.append(hit)
    if resolved:
        await repo.link_co_occurrence(qa_id, session_id, resolved)
        async with session_scope() as s:
            await s.execute(
                update(QAStep).where(QAStep.qa_id == qa_id)
                .values(extracted_concept_ids=[c["concept_id"] for c in resolved])
            )


def _keyword_extract(text: str) -> list[str]:
    """极简关键词抽取（L3 回退用，非主路径）。"""
    # 去停用词 / 取名词短语——这里仅按长度与标切分做占位
    import re
    tokens = [t.strip() for t in re.split(r"[，。、；\s]+", text) if 2 <= len(t.strip()) <= 12]
    return tokens[:5]
