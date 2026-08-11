"""推理框架层契约 InferenceClient（协议 §2.3 / 设计规格 §2.3）。

推理框架工程师实现以下契约，Harness 据此消费流。当前用 StubInferenceClient
跑通编排，联调时替换为真实 client 注入（注入点见 InferenceSessionManager）。

契约方法：
- stream(req) -> AsyncIterator[StreamChunk]  流式产出原始 token（含 sentinel，由 Harness 切分）
- abort(call_id)                              向推理层发取消（用户回上层时调用）
- extract_only(text, model) -> ConceptBlock   轻量抽取调用（L2 拆分 / L1 异步补标注复用）

注意：stream 产出的 chunk 自带 call_id，供 abort；首 token 未到时 call_id 未知，
Harness 通过 aclose 取消生成器（见 InferenceSession._safe_abort）。
"""
from __future__ import annotations

import asyncio
import json
import uuid
from dataclasses import dataclass, field
from typing import AsyncIterator, Optional, Protocol

from app.harness.models import StreamChunk, InferenceRequest, SENTINEL
from app.schemas import ConceptBlock, ConceptItem


class InferenceClient(Protocol):
    """推理框架层契约（待推理框架工程师注入真实实现）。"""

    async def stream(self, req: InferenceRequest) -> AsyncIterator[StreamChunk]:
        ...

    async def abort(self, call_id: str) -> None:
        ...

    async def extract_only(self, text: str, model: Optional[str] = None) -> ConceptBlock:
        ...


@dataclass
class _Script:
    """StubInferenceClient 的可编排脚本，便于单测覆盖各降级路径。"""
    answer_chunks: list[str] = field(default_factory=lambda: ["关于这个问题，", "核心在于……"])
    concept_block: Optional[ConceptBlock] = None
    # 故障注入
    first_token_delay: float = 0.0      # 首 token 延迟（>5s 触发首 token 超时）
    chunk_delay: float = 0.0            # 每个 chunk 间隔（可拉长触发整体 60s 超时）
    json_fail: bool = False             # sentinel 后产出非法 JSON（L1）
    no_sentinel: bool = False           # 完全不产出结构化部分（L1 无标注）
    raise_on_stream: Optional[Exception] = None  # stream 抛异常
    extract_fail: bool = False          # extract_only 抛异常
    extract_block: Optional[ConceptBlock] = None  # extract_only 返回值
    # resume 支持：跳过前 resume_offset 字符正文
    resume_aware: bool = True


def _default_block() -> ConceptBlock:
    return ConceptBlock(
        concepts=[ConceptItem(name="概念A", aliases=["concept_a"], confidence=0.9)],
        model="stub-llm",
    )


class StubInferenceClient:
    """可编排桩 InferenceClient，覆盖 L0/L1/L2/超时/异常路径。

    每个脚本独立 call_id；abort 通过取消内部任务实现。resume_aware 时按
    req.resume_offset 跳过已落盘正文 prefix，模拟「推理调用不重启」续推。
    """

    def __init__(self, scripts: Optional[dict[str, _Script]] = None,
                 default: Optional[_Script] = None):
        # 按 endpoint 取脚本；未配置用 default
        self._scripts = scripts or {}
        self._default = default or _Script(concept_block=_default_block())
        self._live: dict[str, asyncio.Task] = {}

    def script_for(self, endpoint: str) -> _Script:
        return self._scripts.get(endpoint, self._default)

    async def stream(self, req: InferenceRequest) -> AsyncIterator[StreamChunk]:
        script = self.script_for(req.endpoint)
        call_id = f"call_{uuid.uuid4().hex[:8]}"
        # 把生成逻辑放到一个可被 abort 取消的协程里
        gen = self._gen(req, script, call_id)
        try:
            async for chunk in gen:
                yield chunk
        finally:
            self._live.pop(call_id, None)

    async def _gen(self, req: InferenceRequest, script: _Script, call_id: str) -> AsyncIterator[StreamChunk]:
        if script.raise_on_stream is not None:
            raise script.raise_on_stream
        if script.first_token_delay:
            await asyncio.sleep(script.first_token_delay)
        chunks = list(script.answer_chunks)
        # resume：跳过已落盘正文 prefix（协议 §7.2 推理调用不重启）
        if script.resume_aware and req.resume_offset > 0:
            chunks = _skip_prefix(chunks, req.resume_offset)
        for i, c in enumerate(chunks):
            if script.chunk_delay:
                await asyncio.sleep(script.chunk_delay)
            yield StreamChunk(call_id=call_id, delta=c,
                              finish_reason="stop" if i == len(chunks) - 1 and script.no_sentinel else None)
        if script.no_sentinel:
            return
        # sentinel 独占一行
        if script.chunk_delay:
            await asyncio.sleep(script.chunk_delay)
        yield StreamChunk(call_id=call_id, delta=SENTINEL)
        # JSON 段
        block = script.concept_block if script.concept_block is not None else _default_block()
        if script.json_fail:
            yield StreamChunk(call_id=call_id, delta="{ not valid json ,, ", finish_reason="stop")
            return
        yield StreamChunk(call_id=call_id, delta=block.model_dump_json(), finish_reason="stop")

    async def abort(self, call_id: str) -> None:
        task = self._live.pop(call_id, None)
        if task and not task.done():
            task.cancel()

    async def extract_only(self, text: str, model: Optional[str] = None) -> ConceptBlock:
        script = self._default
        if script.extract_fail:
            raise RuntimeError("extract_only failed (stub)")
        if script.extract_block is not None:
            return script.extract_block
        # 默认：从 text 关键词构造一个概念块
        return ConceptBlock(
            concepts=[ConceptItem(name="补标注概念", aliases=[], confidence=0.6)],
            model=model or "stub-extract",
        )


def _skip_prefix(chunks: list[str], offset: int) -> list[str]:
    """按字符 offset 跳过正文 prefix，返回剩余 chunk 列表（resume 续推）。"""
    out: list[str] = []
    skipped = 0
    for c in chunks:
        if skipped >= offset:
            out.append(c)
            continue
        if skipped + len(c) <= offset:
            skipped += len(c)
            continue
        remain = c[offset - skipped:]
        out.append(remain)
        skipped = offset
    return out
