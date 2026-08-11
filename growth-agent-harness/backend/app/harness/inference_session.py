"""生产级 InferenceSession 封装（设计规格 §二 / 架构文档 §7.1 / 协议 §3 §5）。

封装「流式读取 + sentinel 检测 + 降级判定 + checkpoint」，让 QAStep 状态机只
关心业务语义。本类实现主仓 InferenceSession Protocol（stream() 产出
{kind:'delta'|'sentinel'|'json_done'|'error'}），可直接替换 StubInferenceSession
被 QAStepPipeline 消费——这是与 Agent 研发工程师主仓的集成契约。

内部职责：
- 经 ResilientCaller 选端点（主 + failover），首 token 5s 超时重试一次（幂等）
- SentinelDetector 跨 chunk 切分 -> 正文增量 / sentinel / JSON 累积
- 整体 60s 熔断进 L1；JSON 15s 上限；JSON 失败先做一次 extract_only 结构化重试
- 正文一旦开始流式渲染 -> 只降级不重试（架构文档 §7.3 硬约束）
- checkpoint 增量落盘 + last_event_id；L1 触发 on_degrade 回调（manager 入补标注队列）

事件 kind 与设计规格事件映射：
  delta -> ANSWER_TOKEN；sentinel -> (内部切 JSON 态)；
  json_done -> CONCEPTS；error(degrade=L1) -> DEGRADE；流结束 -> COMPLETE
"""
from __future__ import annotations

import asyncio
import logging
from dataclasses import replace
from typing import AsyncIterator, Callable, Optional

from app.harness.models import (
    SENTINEL, Checkpoint, DegradeLevel, HarnessTimeouts, InferenceRequest,
    JsonState, SessionStatus, StreamChunk,
)
from app.harness.sentinel import JsonAccumulator, SentinelDetector
from app.harness.circuit_breaker import ResilientCaller, RetryPolicy
from app.schemas import ConceptBlock

log = logging.getLogger(__name__)


class _OverallTimeout(Exception):
    pass


class _JsonTimeout(Exception):
    pass


# 降级回调：manager 注入，L1 时入补标注队列
OnDegrade = Callable[[Checkpoint, str, str], "asyncio.Awaitable[None]"]
# 可观测回调：记录指标
OnMetric = Callable[[str, dict], None]


class InferenceSession:
    """生产级推理会话封装（实现主仓 InferenceSession Protocol）。"""

    def __init__(
        self,
        session_id: str,
        qa_id: str,
        req: InferenceRequest,
        client,
        caller: ResilientCaller,
        retry: RetryPolicy,
        timeouts: Optional[HarnessTimeouts] = None,
        checkpoint_store=None,
        on_degrade: Optional[OnDegrade] = None,
        on_metric: Optional[OnMetric] = None,
        sentinel: str = SENTINEL,
        checkpoint_every: int = 8,
    ):
        self.session_id = session_id
        self.qa_id = qa_id
        self.req = req
        self.client = client
        self.caller = caller
        self.retry = retry
        self.timeouts = timeouts or HarnessTimeouts()
        self.checkpoint_store = checkpoint_store
        self.on_degrade = on_degrade
        self.on_metric = on_metric
        self.sentinel = sentinel
        self.checkpoint_every = checkpoint_every

        self.checkpoint = Checkpoint(session_id=session_id, qa_id=qa_id, endpoint=req.endpoint)
        self._event_id = 0
        self._body_started = False
        self._consumed = False
        self._live_iter = None
        self._live_call_id: Optional[str] = None

    # —— 主仓 Protocol：QAStepPipeline 消费的语义事件流 ——
    async def stream(self) -> AsyncIterator[dict]:
        if self._consumed:
            raise RuntimeError("InferenceSession.stream() already consumed")
        self._consumed = True
        self.checkpoint.status = SessionStatus.STREAMING.value
        await self._persist()

        attempt = 0
        while True:  # 仅首 token 超时重试
            endpoint, _forced = self.caller.select_endpoint(self.req)
            req = replace(self.req, endpoint=endpoint)
            self.checkpoint.endpoint = endpoint
            it = self.client.stream(req).__aiter__()
            self._live_iter = it
            call_id: Optional[str] = None
            try:
                first = await asyncio.wait_for(
                    it.__anext__(), self.timeouts.first_token_s
                )
            except StopAsyncIteration:
                # 空流：正文未渲染，按 L1 降级
                self.caller.record_success(endpoint)
                await self._finish_no_structured(endpoint, "empty stream")
                yield {"kind": "error", "message": "L1: empty stream", "degrade": "L1"}
                return
            except asyncio.TimeoutError:
                await self._abort_live(call_id)
                self.caller.record_failure(endpoint)
                if self.retry.allow_first_token_retry(attempt, self._body_started):
                    attempt += 1
                    self._metric("first_token_timeout_retry", {"endpoint": endpoint, "attempt": attempt})
                    continue
                await self._finish_degrade(endpoint, "L1", "first-token timeout (5s)")
                yield {"kind": "error", "message": "first-token timeout (5s)",
                       "degrade": "L1", "endpoint": endpoint}
                return
            except Exception as e:  # 推理层异常
                await self._abort_live(call_id)
                self.caller.record_failure(endpoint)
                if self.retry.allow_first_token_retry(attempt, self._body_started):
                    attempt += 1
                    self._metric("stream_error_retry", {"endpoint": endpoint, "attempt": attempt})
                    continue
                await self._finish_degrade(endpoint, "L1", f"stream error: {e}")
                yield {"kind": "error", "message": f"stream error: {e}",
                       "degrade": "L1", "endpoint": endpoint}
                return

            call_id = first.call_id
            self._live_call_id = call_id
            self.checkpoint.call_id = call_id
            loop = asyncio.get_running_loop()
            deadline = loop.time()
            try:
                async for ev in self._drain(it, first, endpoint, deadline):
                    yield ev
                    if ev.get("kind") == "error":
                        return
            except _OverallTimeout:
                self.caller.record_failure(endpoint)
                # 正文已开始 -> 只降级不重试
                await self._finish_degrade(endpoint, "L1", "overall timeout (60s)")
                yield {"kind": "error", "message": "overall timeout (60s)",
                       "degrade": "L1", "endpoint": endpoint}
                return
            except _JsonTimeout:
                self.caller.record_failure(endpoint)
                await self._finish_degrade(endpoint, "L1", "json timeout (15s)")
                yield {"kind": "error", "message": "json timeout (15s)",
                       "degrade": "L1", "endpoint": endpoint}
                return
            except Exception as e:
                # drain 中任意未预期异常：调用已开始（首块已到）-> 只降级不重试
                self.caller.record_failure(endpoint)
                await self._finish_degrade(endpoint, "L1", f"stream error: {e}")
                yield {"kind": "error", "message": f"stream error: {e}",
                       "degrade": "L1", "endpoint": endpoint}
                return
            return  # 正常结束

    async def _drain(self, it, first: StreamChunk, endpoint: str, deadline_start: float) -> AsyncIterator[dict]:
        detector = SentinelDetector(self.sentinel)
        accum: Optional[JsonAccumulator] = None
        json_start: Optional[float] = None
        deltas_since_persist = 0

        async def handle(chunk: StreamChunk):
            nonlocal accum, json_start, deltas_since_persist
            if detector.hit and accum is not None:
                # JSON 累积阶段
                if json_start is None:
                    json_start = asyncio.get_running_loop().time()
                elif (asyncio.get_running_loop().time() - json_start) > self.timeouts.json_s:
                    raise _JsonTimeout()
                accum.feed(chunk.delta)
                return
            text, hit = detector.feed(chunk.delta)
            if text:
                await self._emit_delta(text, endpoint)
                deltas_since_persist += 1
                yield {"kind": "delta", "text": text}
            if hit:
                # sentinel 命中 -> 进入 JSON 累积
                accum = JsonAccumulator()
                json_start = asyncio.get_running_loop().time()
                starter = detector.drain_json()
                if starter:
                    accum.feed(starter)
                self.checkpoint.sentinel_position = self.checkpoint.offset
                self.checkpoint.json_state = JsonState.ACCUMULATING.value
                await self._persist()
                yield {"kind": "sentinel"}

        # 处理首块
        async for ev in handle(first):
            yield ev
        # 后续块，受整体 60s 截止约束
        loop = asyncio.get_running_loop()
        while True:
            remaining = self.timeouts.overall_s - (loop.time() - deadline_start)
            if remaining <= 0:
                raise _OverallTimeout()
            try:
                chunk = await asyncio.wait_for(it.__anext__(), remaining)
            except StopAsyncIteration:
                break
            except asyncio.TimeoutError:
                raise _OverallTimeout()
            async for ev in handle(chunk):
                yield ev
                if ev.get("kind") == "error":
                    return
            if deltas_since_persist >= self.checkpoint_every:
                await self._persist()
                deltas_since_persist = 0

        # 流结束：判定 JSON
        if detector.hit and accum is not None:
            block = accum.try_parse()
            if block is not None:
                self.caller.record_success(endpoint)
                await self._complete_with_concepts(block)
                yield {"kind": "json_done", "block": block}
                return
            # JSON 失败 -> 一次 extract_only 结构化重试（协议 §4.3，15s 上限）
            recovered = await self._structured_retry(endpoint)
            if recovered is not None:
                self.caller.record_success(endpoint)
                await self._complete_with_concepts(recovered)
                yield {"kind": "json_done", "block": recovered}
                return
            self.caller.record_failure(endpoint)
            self.checkpoint.json_state = JsonState.FAILED.value
            self.checkpoint.raw_json = accum.raw
            await self._finish_degrade(endpoint, "L1", "structured JSON parse failed")
            yield {"kind": "error", "message": "L1: structured JSON failed",
                   "degrade": "L1", "endpoint": endpoint}
            return
        # 无 sentinel：正文完整但无结构化 -> L1（不报 error，正文正常展示）
        tail = detector.flush_answer()
        if tail:
            await self._emit_delta(tail, endpoint)
            yield {"kind": "delta", "text": tail}
        self.caller.record_success(endpoint)  # 正文已交付
        await self._finish_no_structured(endpoint, "no structured part")

    async def _structured_retry(self, endpoint: str) -> Optional[ConceptBlock]:
        """JSON 失败后一次结构化重试：extract_only(已产出正文)，15s 上限（协议 §4.3）。"""
        self._metric("structured_retry_attempt", {"endpoint": endpoint, "qa_id": self.qa_id})
        try:
            block = await asyncio.wait_for(
                self.client.extract_only(self.checkpoint.answer_checkpoint, self.req.model),
                self.timeouts.json_s,
            )
            self.caller.record_success(endpoint)
            return block
        except asyncio.TimeoutError:
            self.caller.record_failure(endpoint)
            return None
        except Exception as e:
            self.caller.record_failure(endpoint)
            self._metric("structured_retry_error", {"endpoint": endpoint, "error": str(e)[:120]})
            return None

    # —— checkpoint / 事件 / 降级 ——
    async def _emit_delta(self, text: str, endpoint: str) -> None:
        if not self._body_started:
            self._body_started = True
            self._metric("first_token", {"endpoint": endpoint, "qa_id": self.qa_id})
        self.checkpoint.answer_checkpoint += text
        self.checkpoint.offset = len(self.checkpoint.answer_checkpoint)
        self._event_id += 1
        self.checkpoint.last_event_id = self._event_id

    async def _complete_with_concepts(self, block: ConceptBlock) -> None:
        self.checkpoint.json_state = JsonState.PARSED.value
        self.checkpoint.degrade_level = DegradeLevel.L0.value
        self.checkpoint.status = SessionStatus.COMPLETED.value
        self.checkpoint.concept_ids = [c.name for c in block.concepts]
        await self._persist()
        self._metric("complete", {"qa_id": self.qa_id, "degrade": "L0",
                                  "concepts": len(block.concepts)})

    async def _finish_no_structured(self, endpoint: str, reason: str) -> None:
        """正文完整但无结构化（L1），不阻断正文展示。"""
        self.checkpoint.degrade_level = DegradeLevel.L1.value
        self.checkpoint.status = SessionStatus.COMPLETED.value
        await self._persist()
        await self._fire_degrade("L1", reason)
        self._metric("complete", {"qa_id": self.qa_id, "degrade": "L1", "reason": reason})

    async def _finish_degrade(self, endpoint: str, level: str, reason: str) -> None:
        self.checkpoint.degrade_level = level
        if self._body_started:
            self.checkpoint.status = SessionStatus.COMPLETED.value
        await self._persist()
        await self._fire_degrade(level, reason)
        self._metric("degrade", {"qa_id": self.qa_id, "level": level, "reason": reason})

    async def _fire_degrade(self, level: str, reason: str) -> None:
        if level == "L1" and self.on_degrade is not None:
            try:
                await self.on_degrade(self.checkpoint, level, reason)
            except Exception:
                log.exception("on_degrade callback failed qa_id=%s", self.qa_id)

    async def _persist(self) -> None:
        if self.checkpoint_store is not None:
            try:
                await self.checkpoint_store.save(self.checkpoint)
            except Exception:
                log.exception("checkpoint save failed qa_id=%s", self.qa_id)

    def _metric(self, name: str, payload: dict) -> None:
        if self.on_metric is not None:
            try:
                self.on_metric(name, payload)
            except Exception:
                pass

    async def _abort_live(self, call_id: Optional[str]) -> None:
        """向推理层发取消；首 token 未到时 call_id 未知，靠 aclose 取消生成器。"""
        if call_id:
            try:
                await self.client.abort(call_id)
            except Exception:
                pass
        if self._live_iter is not None:
            try:
                await self._live_iter.aclose()
            except Exception:
                pass

    async def abort(self) -> dict:
        """用户回上层：向推理层发 abort + 已流出正文落盘保留（设计规格 §三）。"""
        await self._abort_live(self._live_call_id)
        self.checkpoint.status = SessionStatus.INTERRUPTED.value
        await self._persist()
        self._metric("abort", {"qa_id": self.qa_id, "offset": self.checkpoint.offset})
        return self.checkpoint.snapshot()

    def state(self) -> dict:
        return self.checkpoint.snapshot()
