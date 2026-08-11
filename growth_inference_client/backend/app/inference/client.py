"""真实 InferenceClient —— 实现主仓 InferenceSession Protocol，替换 StubInferenceSession。

按协议设计文档实现：
- 单次调用产出「流式正文 + 尾部 sentinel 分隔的结构化 JSON」，跨 chunk 检测 sentinel；
- 约束解码（outlines/xgrammar 优先，不支持时退化为正则 + Pydantic + 有限重试）；
- L0-L3 降级链路；
- 上下文膨胀控制（深层 prompt ≤ 2K token）。

事件契约对齐主仓 app.inference.protocol.InferenceSession：
  {kind:'delta', text}        正文增量
  {kind:'sentinel'}           正文结束，进入 JSON 累积（QAStep 据 generating->extracting）
  {kind:'json_done', block}   尾部 JSON 解析完成（ConceptBlock）→ L0
  {kind:'error', message, level, needs_backfill}  失败 → L1/L3

QAStepPipeline 只读 kind/text/message/block，额外 level/needs_backfill 字段供观测，不影响消费。
"""
from __future__ import annotations

import asyncio
import hashlib
import logging
from typing import AsyncIterator, Callable, Awaitable, Optional

from app.config import settings
from app.schemas import ConceptBlock
from app.inference.sentinel import SentinelDetector, build_full_sentinel
from app.inference.constraints import ConstrainedDecoder, keyword_fallback
from app.inference.context import ContextBudget, LayerSummary
from app.inference.backend import LLMBackend

log = logging.getLogger(__name__)

# 完整 sentinel（带首尾换行）
FULL_SENTINEL = build_full_sentinel(settings.concept_sentinel)

BackfillHook = Callable[[str], Awaitable[None]]


class InferenceClient:
    """真实推理会话，实现 InferenceSession Protocol。

    可直接注入 QAStepPipeline 替换 StubInferenceSession：
        session = InferenceClient(qa_id, session_id, question, backend=llm, ...)
        pipeline = QAStepPipeline(qa_id, session_id, question, session, normalizer, repo)

    降级链路（协议第五节）：
    - L0：单次调用成功产出正文 + 结构化概念 → delta* / sentinel / json_done
    - L1：JSON 解析失败（含超时）→ delta* / sentinel / error(L1, needs_backfill)，
          正文完整保留，异步补标注由 backfill_hook 落库
    - L2：模型不支持流式+结构化并存（co_streaming_supported=False）→ 拆两次调用
    - L3：L2 第二次抽取也失败 → 关键词匹配兜底；匹配到则 json_done，否则 error(L3)
    """

    def __init__(
        self,
        qa_id: str,
        session_id: str,
        question: str,
        *,
        backend: LLMBackend,
        model: str = "default",
        extract_model: Optional[str] = None,
        parent_chain: Optional[list[str]] = None,
        layer_summaries: Optional[list[LayerSummary]] = None,
        concept_table: Optional[list[dict]] = None,
        constrained: Optional[ConstrainedDecoder] = None,
        context_budget: Optional[ContextBudget] = None,
        backfill_hook: Optional[BackfillHook] = None,
        co_streaming_supported: bool = True,
        temperature: float = 0.7,
        max_answer_tokens: int = 2048,
        first_token_timeout: Optional[float] = None,
        overall_timeout: Optional[float] = None,
        json_timeout: Optional[float] = None,
    ):
        self.qa_id = qa_id
        self.session_id = session_id
        self.question = question

        self.backend = backend
        self.model = model
        self.extract_model = extract_model or model
        self.parent_chain = parent_chain or []
        self.layer_summaries = layer_summaries or []
        self.concept_table = concept_table or []
        self.constrained = constrained or ConstrainedDecoder(guided_supported=False)
        self.ctx = context_budget or ContextBudget()
        self.backfill_hook = backfill_hook

        self.co_streaming_supported = co_streaming_supported
        self.temperature = temperature
        self.max_answer_tokens = max_answer_tokens
        self.first_token_timeout = first_token_timeout or settings.first_token_timeout_s
        self.overall_timeout = overall_timeout or settings.inference_timeout_s
        self.json_timeout = json_timeout or settings.json_parse_timeout_s

    # ------------------------------------------------------------------ Protocol
    async def stream(self) -> AsyncIterator[dict]:
        """产出事件序列，对齐 InferenceSession Protocol。"""
        if self.co_streaming_supported:
            async for ev in self._single_call_path():
                yield ev
        else:
            async for ev in self._two_call_path():
                yield ev

    # ------------------------------------------------------------------ L0 / L1
    async def _single_call_path(self) -> AsyncIterator[dict]:
        messages = self.ctx.build_messages(
            self.question, self.parent_chain, self.layer_summaries
        )
        detector = SentinelDetector(FULL_SENTINEL)
        answer_parts: list[str] = []
        sentinel_emitted = False
        first_token_seen = False

        try:
            async for chunk in self._stream_with_timeout(messages, first_token_only=False):
                first_token_seen = True
                text, hit = detector.feed(chunk)
                if text:
                    answer_parts.append(text)
                    yield {"kind": "delta", "text": text}
                if hit and not sentinel_emitted:
                    sentinel_emitted = True
                    yield {"kind": "sentinel"}
            # flush
            text, hit = detector.flush()
            if text:
                answer_parts.append(text)
                yield {"kind": "delta", "text": text}
            if hit and not sentinel_emitted:
                sentinel_emitted = True
                yield {"kind": "sentinel"}
        except asyncio.TimeoutError:
            # 整体超时 → 熔断 L1（协议 7.3）
            if not first_token_seen:
                # 首 token 超时且正文未渲染 → 幂等重试一次
                log.warning("first-token timeout qa_id=%s, retry once", self.qa_id)
                async for ev in self._single_call_path():
                    yield ev
                return
            # 正文已流出 → 只能降级
            if not sentinel_emitted:
                sentinel_emitted = True
                yield {"kind": "sentinel"}
            await self._trigger_backfill()
            yield {
                "kind": "error",
                "message": "overall timeout (L1)",
                "level": "L1",
                "needs_backfill": True,
            }
            return
        except Exception as e:
            log.exception("inference stream error qa_id=%s", self.qa_id)
            if not sentinel_emitted:
                sentinel_emitted = True
                yield {"kind": "sentinel"}
            await self._trigger_backfill()
            yield {
                "kind": "error",
                "message": f"inference error (L1): {e}",
                "level": "L1",
                "needs_backfill": True,
            }
            return

        # 正文已全部流出；若模型未产 sentinel，补一个合成 sentinel 让 QAStep 进入 extracting
        if not sentinel_emitted:
            sentinel_emitted = True
            yield {"kind": "sentinel"}

        answer_text = "".join(answer_parts)

        # 解析 JSON 段
        block = self.constrained.extract(detector.json_text)
        if block is not None:
            yield {"kind": "json_done", "block": self._annotate(block, answer_text)}
            return

        # 降级路径：正则失败 → 单次重试（仅结构化部分，正文不动）
        block = await self._retry_extract(answer_text)
        if block is not None:
            yield {"kind": "json_done", "block": self._annotate(block, answer_text)}
            return

        # 仍失败 → L1
        await self._trigger_backfill()
        yield {
            "kind": "error",
            "message": "json parse failed (L1)",
            "level": "L1",
            "needs_backfill": True,
        }

    # ------------------------------------------------------------------ L2 / L3
    async def _two_call_path(self) -> AsyncIterator[dict]:
        """L2：模型不支持流式+结构化并存，拆两次调用。"""
        # 第一次：纯流式回答（不要求 JSON）
        messages = self.ctx.build_messages(
            self.question, self.parent_chain, self.layer_summaries,
            system_prompt=_ANSWER_ONLY_SYSTEM_PROMPT,
        )
        answer_parts: list[str] = []
        first_token_seen = False
        try:
            async for chunk in self._stream_with_timeout(messages, first_token_only=False):
                first_token_seen = True
                answer_parts.append(chunk)
                yield {"kind": "delta", "text": chunk}
        except asyncio.TimeoutError:
            if not first_token_seen:
                log.warning("L2 first-token timeout qa_id=%s, retry once", self.qa_id)
                async for ev in self._two_call_path():
                    yield ev
                return
            yield {"kind": "sentinel"}
            await self._trigger_backfill()
            yield {
                "kind": "error",
                "message": "L2 answer timeout (L1)",
                "level": "L1",
                "needs_backfill": True,
            }
            return
        except Exception as e:
            log.exception("L2 answer stream error qa_id=%s", self.qa_id)
            yield {"kind": "sentinel"}
            await self._trigger_backfill()
            yield {
                "kind": "error",
                "message": f"L2 answer error (L1): {e}",
                "level": "L1",
                "needs_backfill": True,
            }
            return

        answer_text = "".join(answer_parts)
        yield {"kind": "sentinel"}  # 正文完成，进入 extracting

        # 第二次：轻量抽取调用（可用更小模型）
        block = await self._extract_call(answer_text)
        if block is not None:
            yield {"kind": "json_done", "block": self._annotate(block, answer_text)}
            return

        # L3：关键词匹配兜底
        block = keyword_fallback(answer_text, self.concept_table)
        if block is not None:
            yield {"kind": "json_done", "block": self._annotate(block, answer_text)}
            return

        # L3 也空 → 无标注，正文完整返回
        await self._trigger_backfill()
        yield {
            "kind": "error",
            "message": "extraction failed, keyword fallback empty (L3)",
            "level": "L3",
            "needs_backfill": True,
        }

    # ------------------------------------------------------------------ helpers
    async def _stream_with_timeout(
        self, messages: list[dict], *, first_token_only: bool
    ) -> AsyncIterator[str]:
        """带首 token 超时 + 整体超时的流式包装。"""
        queue: asyncio.Queue[Optional[str]] = asyncio.Queue()
        done_flag = object()

        async def producer():
            try:
                async for ch in self.backend.stream(
                    messages,
                    model=self.model,
                    temperature=self.temperature,
                    max_tokens=self.max_answer_tokens,
                    guided=None,  # 主调用不约束（正文需自由生成）
                    timeout=self.overall_timeout,
                ):
                    await queue.put(ch)
            except Exception as e:
                await queue.put(e)
            finally:
                await queue.put(done_flag)  # type: ignore[arg-type]

        task = asyncio.create_task(producer())
        first_received = False
        try:
            while True:
                if not first_received:
                    try:
                        item = await asyncio.wait_for(
                            queue.get(), timeout=self.first_token_timeout
                        )
                    except asyncio.TimeoutError:
                        task.cancel()
                        raise
                else:
                    try:
                        item = await asyncio.wait_for(
                            queue.get(), timeout=self.overall_timeout
                        )
                    except asyncio.TimeoutError:
                        task.cancel()
                        raise
                if item is done_flag:
                    return
                if isinstance(item, Exception):
                    raise item
                first_received = True
                yield item  # type: ignore[misc]
        finally:
            if not task.done():
                task.cancel()
                try:
                    await task
                except (asyncio.CancelledError, Exception):
                    pass

    async def _retry_extract(self, answer_text: str) -> Optional[ConceptBlock]:
        """正则降级失败后的单次重试：仅重抽概念块，不重新生成正文（协议 4.2-4.3）。"""
        messages = self.ctx.build_extract_messages(
            answer_text, self.question, self.parent_chain
        )
        guided = self.constrained.guided_params()
        try:
            raw = await asyncio.wait_for(
                self.backend.complete(
                    messages,
                    model=self.extract_model,
                    temperature=max(0.0, self.temperature - 0.2),
                    max_tokens=1024,
                    guided=guided,
                    timeout=self.overall_timeout,
                ),
                timeout=self.json_timeout,
            )
        except (asyncio.TimeoutError, Exception) as e:
            log.warning("retry extract failed qa_id=%s: %s", self.qa_id, e)
            return None
        return self.constrained.extract(raw)

    async def _extract_call(self, answer_text: str) -> Optional[ConceptBlock]:
        """L2 第二次抽取调用（可带约束）。"""
        messages = self.ctx.build_extract_messages(
            answer_text, self.question, self.parent_chain
        )
        guided = self.constrained.guided_params()
        try:
            raw = await asyncio.wait_for(
                self.backend.complete(
                    messages,
                    model=self.extract_model,
                    temperature=max(0.0, self.temperature - 0.2),
                    max_tokens=1024,
                    guided=guided,
                    timeout=self.json_timeout,
                ),
                timeout=self.json_timeout,
            )
        except (asyncio.TimeoutError, Exception) as e:
            log.warning("L2 extract call failed qa_id=%s: %s", self.qa_id, e)
            return None
        return self.constrained.extract(raw)

    def _annotate(self, block: ConceptBlock, answer_text: str) -> ConceptBlock:
        """回填埋点字段：model / prompt_hash（QAStep 埋点依赖）。"""
        if not block.model or block.model == "unknown":
            block.model = self.model
        block.prompt_hash = _prompt_hash(self.question, self.parent_chain)
        return block

    async def _trigger_backfill(self) -> None:
        """L1/L3 触发「待补标注」落库（协议 5.3）。"""
        if self.backfill_hook is None:
            return
        try:
            await self.backfill_hook(self.qa_id)
        except Exception:
            log.exception("backfill hook failed qa_id=%s", self.qa_id)


_ANSWER_ONLY_SYSTEM_PROMPT = """你是一个学习辅导助手。请用自然语言回答用户问题。
只输出正文，不要输出任何 JSON、标记或结构化内容。"""


def _prompt_hash(question: str, parent_chain: list[str]) -> str:
    blob = question + "|" + ":".join(parent_chain)
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()[:16]
