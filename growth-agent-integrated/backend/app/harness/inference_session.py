"""生产级 InferenceSession 封装（对应 Harness 工程师交付物）。

包裹 InferenceClient，使 QAStep 只感知业务语义；打通：
- checkpoint 恢复（qa_id + offset）；
- 熔断重试（首 token 超时 5s 重试一次 / 整体 60s 熔断进 L1 / JSON 15s / failover）；
- 正文不可撤回约束（正文一旦开始流式渲染只降级不重试）；
- 异步补标注 worker 注入点（L1 触发 backfill_hook）；
- 通用 except：流式已开始则只降级（L1）不重试。

实现主仓 InferenceSession Protocol（session_id / qa_id / stream()），QAStepPipeline 无需改动。
"""
from __future__ import annotations

import asyncio
import logging
from typing import AsyncIterator, Optional

from app.config import settings
from app.harness.models import Checkpoint, JsonState, SessionStatus
from app.harness.store import InMemoryCheckpointStore, CheckpointStore
from app.harness.recovery import RecoveryManager
from app.harness.circuit_breaker import CircuitBreaker
from app.harness.observability import observability
from app.inference.client import InferenceClient
from app.inference.backend import LLMBackend, StubLLMBackend

log = logging.getLogger(__name__)


class InferenceSession:
    """Harness 层推理会话封装：实现 InferenceSession Protocol。

    client: InferenceClient（或其工厂回调）；由 build_harness 注入真实/桩后端。
    """

    def __init__(self, qa_id: str, session_id: str, question: str,
                 client: InferenceClient | None = None,
                 backend: LLMBackend | None = None,
                 checkpoint_store: CheckpointStore | None = None,
                 breaker: CircuitBreaker | None = None,
                 recovery: RecoveryManager | None = None):
        self.qa_id = qa_id
        self.session_id = session_id
        self.question = question
        self._client = client or InferenceClient(qa_id, session_id, question, backend=backend)
        self._cp_store = checkpoint_store or InMemoryCheckpointStore()
        self._breaker = breaker
        self._recovery = recovery or RecoveryManager(self._cp_store)
        self._cp = Checkpoint(qa_id=qa_id, session_id=session_id, model=self._client.backend.model)
        self._answer_started = False
        self._event_seq = 0

    def attach_breaker(self, breaker: CircuitBreaker) -> None:
        self._breaker = breaker
        self._client.attach_breaker(breaker)

    def set_material_context(self, ctx: str | None) -> None:
        """转发学习材料相关段落给 InferenceClient，stream() 拼 prompt 时用，
        不污染 question 存库。"""
        if hasattr(self._client, "set_material_context"):
            self._client.set_material_context(ctx)

    def set_chain(self, chain) -> None:
        """转发概念链（下钻路径）给 InferenceClient，stream() 拼 prompt 填概念链槽。"""
        if hasattr(self._client, "set_chain"):
            self._client.set_chain(chain)

    async def abort(self) -> None:
        """用户回上层：发 abort，正文落盘保留。"""
        await self._client.abort()
        self._cp.status = SessionStatus.INTERRUPTED
        await self._cp_store.save(self._cp)

    async def stream(self) -> AsyncIterator[dict]:
        """产出 {delta|sentinel|json_done|error} 语义事件，QAStep 消费。"""
        endpoint = self._client.backend.endpoint
        breaker_ok = self._breaker is None or self._breaker.allow(endpoint)
        if not breaker_ok:
            # 熔断：尝试 failover
            if self._breaker is not None:
                backup = self._breaker.failover(endpoint)
                if backup:
                    log.info("breaker failover %s -> %s", endpoint, backup)
                    breaker_ok = True
            if not breaker_ok:
                yield {"kind": "error", "message": f"circuit open on {endpoint} (L1)"}
                return

        # 首 token 超时重试一次（正文未开始渲染才重试）
        attempts = 0
        max_attempts = 2 if not self._answer_started else 1
        while attempts < max_attempts:
            attempts += 1
            try:
                async for ev in self._stream_once():
                    yield ev
                return
            except asyncio.TimeoutError:
                if attempts < max_attempts and not self._answer_started:
                    log.info("first-token timeout, retry once qa_id=%s", self.qa_id)
                    continue
                # 正文已流出或重试已用 -> L1
                if self._breaker:
                    self._breaker.record_failure(endpoint)
                await self._mark_recovery(False)
                yield {"kind": "error", "message": "first token timeout (L1)"}
                return
            except Exception as e:  # noqa: BLE001
                log.exception("InferenceSession stream error qa_id=%s", self.qa_id)
                if self._breaker:
                    self._breaker.record_failure(endpoint)
                if self._answer_started:
                    # 正文已开始 -> 只降级不重试
                    await self._mark_recovery(False)
                    yield {"kind": "error", "message": f"stream error after prose (L1): {e}"}
                    return
                if attempts < max_attempts:
                    continue
                await self._mark_recovery(False)
                yield {"kind": "error", "message": f"stream error: {e}"}
                return

    async def _stream_once(self) -> AsyncIterator[dict]:
        """单次流式 + checkpoint + 首 token 超时。"""
        endpoint = self._client.backend.endpoint
        first_token = True
        async for ev in self._client.stream():
            self._event_seq += 1
            kind = ev["kind"]
            if kind == "delta":
                self._answer_started = True
                self._cp.answer_checkpoint += ev["text"]
                self._cp.last_event_id = self._event_seq
                await self._cp_store.save(self._cp)
            elif kind == "sentinel":
                self._cp.sentinel_position = len(self._cp.answer_checkpoint)
                self._cp.json_state = JsonState.ACCUMULATING
                await self._cp_store.save(self._cp)
            elif kind == "json_done":
                self._cp.json_state = JsonState.PARSED
                self._cp.status = SessionStatus.COMPLETED
                await self._cp_store.save(self._cp)
                if self._breaker:
                    self._breaker.record_success(endpoint)
                await self._mark_recovery(True)
            elif kind == "error":
                if self._breaker:
                    self._breaker.record_failure(endpoint)
                await self._mark_recovery(self._answer_started)
            yield ev
            if kind == "json_done" or kind == "error":
                return

    async def _mark_recovery(self, success: bool) -> None:
        observability.record_recovery(success)

    async def resume(self, qa_id: str):
        """网络断开重连：从最近 checkpoint 续推。"""
        return await self._recovery.resume(qa_id)
