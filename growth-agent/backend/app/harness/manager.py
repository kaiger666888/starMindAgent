"""InferenceSessionManager —— Harness 统一 API（设计规格 §2.2）。

Agent 研发工程师通过 Manager 操作推理会话，无需感知 sentinel / 降级 / checkpoint：
  start(session_id, qa_id, prompt, model)  -> InferenceSession（QAStepPipeline 直接消费）
  abort(qa_id)                              用户回上层：发 abort + 落盘保留现场
  resume(qa_id, last_event_id)              断连重连：重放 checkpoint 事件，推理调用不重启
  get(qa_id) / state()                      查询状态快照

L1 降级时经 on_degrade 回调把补标注任务入持久队列（reannotation.WorkerPool 消费）。
"""
from __future__ import annotations

import logging
from typing import Optional

from app.harness.models import (
    Checkpoint, DegradeLevel, HarnessTimeouts, InferenceRequest, SessionStatus,
)
from app.harness.circuit_breaker import ResilientCaller, RetryPolicy
from app.harness.inference_session import InferenceSession
from app.harness.store import CheckpointStore, InMemoryCheckpointStore

log = logging.getLogger(__name__)


class InferenceSessionManager:
    """推理会话生命周期管理（设计规格 §2.2 统一 API）。"""

    def __init__(
        self,
        client,
        caller: ResilientCaller,
        retry: Optional[RetryPolicy] = None,
        timeouts: Optional[HarnessTimeouts] = None,
        checkpoint_store: Optional[CheckpointStore] = None,
        reannotation_queue=None,
        on_metric=None,
    ):
        self.client = client
        self.caller = caller
        self.retry = retry or RetryPolicy()
        self.timeouts = timeouts or HarnessTimeouts()
        self.checkpoint_store = checkpoint_store or InMemoryCheckpointStore()
        self.reannotation_queue = reannotation_queue
        self.on_metric = on_metric
        self._live: dict[str, InferenceSession] = {}

    async def start(
        self,
        session_id: str,
        qa_id: str,
        prompt: str,
        model: str = "default",
        endpoint: str = "primary",
        resume_offset: int = 0,
    ) -> InferenceSession:
        """启动会话，返回 InferenceSession（实现主仓 Protocol，QAStepPipeline 直接消费）。"""
        req = InferenceRequest(
            prompt=prompt, model=model, endpoint=endpoint, resume_offset=resume_offset,
        )
        sess = InferenceSession(
            session_id=session_id, qa_id=qa_id, req=req, client=self.client,
            caller=self.caller, retry=self.retry, timeouts=self.timeouts,
            checkpoint_store=self.checkpoint_store,
            on_degrade=self._on_degrade, on_metric=self.on_metric,
        )
        self._live[qa_id] = sess
        return sess

    async def abort(self, qa_id: str) -> dict:
        """用户主动回上层：向推理层发 abort + 已流出正文落盘保留（设计规格 §三）。"""
        sess = self._live.get(qa_id)
        if sess is not None:
            return await sess.abort()
        # 进程重启后无 live session：从持久化 checkpoint 恢复中断现场
        cp = await self.checkpoint_store.load(qa_id)
        if cp is None:
            return {"qa_id": qa_id, "status": "unknown"}
        cp.status = SessionStatus.INTERRUPTED.value
        await self.checkpoint_store.save(cp)
        return cp.snapshot()

    async def resume(self, qa_id: str, last_event_id: int = 0) -> dict:
        """网络断开重连：从最近 checkpoint 续推，推理调用不重启（设计规格 §三）。

        返回 checkpoint 快照 + 重放事件列表（event_id > last_event_id）。
        内存丢失（进程重启）时从持久化 checkpoint 重水化；live session 仍在则
        取其当前现场。推理调用本身不重启——正文已落盘，前端按 offset 对齐即可。
        """
        sess = self._live.get(qa_id)
        if sess is not None:
            cp = sess.checkpoint
        else:
            cp = await self.checkpoint_store.load(qa_id)
        if cp is None:
            # 与成功路径结构对齐：recovery 层据此判定 clean failure
            return {
                "qa_id": qa_id,
                "checkpoint": {"status": "unknown"},
                "events": [],
                "resume_offset": 0,
            }
        # 重放：从 checkpoint 构造对齐事件（前端丢弃 last-event-id 之后未确认内容）
        events = self._replay_from(cp, last_event_id)
        return {
            "qa_id": qa_id,
            "checkpoint": cp.snapshot(),
            "events": events,
            "resume_offset": cp.offset,
        }

    async def get(self, qa_id: str) -> dict:
        sess = self._live.get(qa_id)
        if sess is not None:
            return sess.state()
        cp = await self.checkpoint_store.load(qa_id)
        return cp.snapshot() if cp else {"qa_id": qa_id, "status": "unknown"}

    def state(self) -> dict:
        """所有 live session 快照（供 /harness/obs/metrics）。"""
        return {qa: s.state() for qa, s in self._live.items()}

    # —— L1 降级 -> 入补标注队列 ——
    async def _on_degrade(self, cp: Checkpoint, level: str, reason: str) -> None:
        if level != DegradeLevel.L1.value:
            return
        if self.reannotation_queue is None:
            return
        await self.reannotation_queue.enqueue(
            qa_id=cp.qa_id,
            session_id=cp.session_id,
            answer_snapshot=cp.answer_checkpoint,
            reason=reason,
        )
        log.info("L1 degrade -> reannotation enqueued qa_id=%s reason=%s", cp.qa_id, reason)

    @staticmethod
    def _replay_from(cp: Checkpoint, last_event_id: int) -> list[dict]:
        """构造 last-event-id 之后的重放事件（恢复协议 §7.2）。"""
        events: list[dict] = []
        if cp.last_event_id <= last_event_id:
            return events
        # 单次重放：把完整正文作为一次 answer_replay 事件交前端对齐
        events.append({
            "type": "answer_replay",
            "qa_id": cp.qa_id,
            "answer": cp.answer_checkpoint,
            "offset": cp.offset,
            "event_id": cp.last_event_id,
        })
        if cp.json_state == "parsed" and cp.concept_ids:
            events.append({
                "type": "concepts_replay",
                "qa_id": cp.qa_id,
                "concept_ids": cp.concept_ids,
                "event_id": cp.last_event_id + 1,
            })
        return events
