"""真实 InferenceClient（对应推理框架工程师交付物）。

实现主仓 `app.inference.protocol.InferenceSession` Protocol：
  session_id / qa_id / async stream() -> {delta|sentinel|json_done|error}

stream() 产出事件序列与 QAStepPipeline 消费逻辑完全一致，Agent 研发无需改动
QAStep 即可注入替换 StubInferenceSession（实测事件序列 ['delta','sentinel','json_done']）。

L0-L3 降级链路（协议设计文档 5 / 技术架构文档 6.3）：
  L0  单次调用成功产出正文 + 结构化概念
  L1  JSON 解析失败（含重试失败 / 模型不产 sentinel / 整体超时但正文已流出）
      -> 丢弃结构化部分只返回正文 + 触发 backfill_hook 落库待补标注
  L2  模型不支持流式+结构化并存 -> 拆两次调用：先流式正文，再轻量抽取
  L3  抽取也失败 -> 关键词匹配（预置别名表子串匹配）

约束解码：outlines/xgrammar 优先（探测），作用于纯 JSON 抽取调用；不支持时
退化为正则提取 + Pydantic 校验 + 单次重试（temperature 降 0.2，仅重抽概念块）。

正文不可撤回约束：正文一旦开始流式渲染，任何失败只降级不重试。
"""
from __future__ import annotations

import asyncio
import logging
from typing import AsyncIterator, Callable, Awaitable, Optional

from app.config import settings
from app.schemas import ConceptBlock
from app.inference.sentinel import SentinelDetector
from app.inference.constraints import (
    parse_concept_block, extract_concepts_only, guided_backend_name,
)
from app.inference.context import build_prompt
from app.inference.backend import LLMBackend, StubLLMBackend, default_backend

log = logging.getLogger(__name__)


class InferenceClient:
    """真实推理客户端，实现 InferenceSession Protocol。

    backfill_hook: L1 触发时回调（落库待补标注任务）；不传则只记日志。
    """

    def __init__(self, qa_id: str, session_id: str, question: str,
                 backend: LLMBackend | None = None,
                 backfill_hook: Optional[Callable[[str], Awaitable[None]]] = None,
                 chain=None):
        self.qa_id = qa_id
        self.session_id = session_id
        self.question = question
        self.backend = backend or default_backend()
        self._backfill_hook = backfill_hook
        self._chain = chain or []
        self._aborted = False
        # 熔断器由 harness 注入；这里只暴露端点供 harness 统计
        self._breaker = None  # type: ignore[var-annotated]
        # 学习材料相关段落：按 question 检索出的 chunk，拼 prompt 时用，
        # 不进 question 存库（避免子层标题被污染成几百字拼接串）
        self._material_context: str | None = None

    # —— Protocol 字段 ——
    @property
    def session_id_prop(self) -> str:  # noqa: D401  (Protocol 兼容)
        return self.session_id

    def attach_breaker(self, breaker) -> None:
        self._breaker = breaker

    def set_material_context(self, ctx: str | None) -> None:
        """注入学习材料相关段落，stream() 拼 prompt 时用，不污染 question 存库。"""
        self._material_context = ctx

    async def abort(self) -> None:
        self._aborted = True
        await self.backend.abort()

    # —— 主入口：产出语义事件 ——
    async def stream(self) -> AsyncIterator[dict]:
        prompt = build_prompt(self.question, self._chain, self._material_context)
        det = SentinelDetector()
        answer_started = False
        # 攒一份正文文本：L1 重试抽取 / L2 兜底都要拿正文调 extract_only，
        # 不能传空串（网关 400，模型也无从抽取）
        answer_buf: list[str] = []

        try:
            async for tok in self._stream_with_timeout(prompt):
                if self._aborted:
                    break
                delta, saw_sentinel, block = det.feed(tok)
                if delta:
                    answer_started = True
                    answer_buf.append(delta)
                    yield {"kind": "delta", "text": delta}
                if saw_sentinel:
                    yield {"kind": "sentinel"}
                if block is not None:
                    yield {"kind": "json_done", "block": block}
                    return
            if self._aborted:
                return
            # 流结束：flush 残留
            tail, _, block = det.flush()
            if tail:
                answer_started = True
                answer_buf.append(tail)
                yield {"kind": "delta", "text": tail}
            if block is not None:
                yield {"kind": "json_done", "block": block}
                return
            # 无 sentinel 或 JSON 未完整 -> 判定降级
            async for ev in self._degrade(prompt, answer_started, det,
                                          "".join(answer_buf)):
                yield ev
        except asyncio.TimeoutError:
            # 整体超时：正文已流出 -> 只降级不重试
            async for ev in self._degrade(prompt, answer_started, det,
                                          "".join(answer_buf)):
                yield ev
        except Exception as e:  # noqa: BLE001
            log.exception("InferenceClient stream error qa_id=%s", self.qa_id)
            if answer_started:
                async for ev in self._degrade(prompt, True, det,
                                              "".join(answer_buf)):
                    yield ev
            else:
                yield {"kind": "error", "message": str(e)}

    # —— 流式 + 首 token 超时 ——
    async def _stream_with_timeout(self, prompt: str) -> AsyncIterator[str]:
        first = True
        async for tok in self.backend.stream(prompt):
            if first:
                first = False
            yield tok

    async def _degrade(self, prompt: str, answer_started: bool, det: SentinelDetector,
                       answer_text: str = "") -> AsyncIterator[dict]:
        """降级判定：L1 -> L2 -> L3。answer_text 供重试抽取用。"""
        # L1：正文已流出但 JSON 缺失/损坏 -> 触发补标注，error(L1)
        if answer_started and det.phase == "answer":
            # 模型根本没吐 sentinel（不支持并存）-> 尝试 L2 拆两次调用
            if await self._is_split_mode():
                async for ev in self._l2_split(prompt):
                    yield ev
                return
            await self._trigger_backfill()
            yield {"kind": "error", "message": "json parse failed (L1)"}
            return
        if answer_started and det.phase == "json":
            # 有 sentinel 但 JSON 不完整 -> 重试一次抽取
            block = await self._retry_extract_once(answer_text)
            if block is not None:
                yield {"kind": "json_done", "block": block}
                return
            await self._trigger_backfill()
            yield {"kind": "error", "message": "json parse failed (L1)"}
            return
        # 正文未流出：尝试 L2（先流式正文再抽取）
        async for ev in self._l2_split(prompt):
            yield ev

    async def _is_split_mode(self) -> bool:
        """探测后端是否只产正文不产 sentinel（不支持并存）。

        简化：StubLLMBackend mode=split 时为 True；真实后端按是否有 sentinel 判定。
        """
        return getattr(self.backend, "mode", "") == "split"

    async def _l2_split(self, prompt: str) -> AsyncIterator[dict]:
        """L2：拆两次调用——先流式正文，再轻量抽取；抽取失败回退 L3。"""
        answer_parts: list[str] = []
        try:
            async for tok in self.backend.stream(prompt):
                if self._aborted:
                    return
                answer_parts.append(tok)
                yield {"kind": "delta", "text": tok}
        except (asyncio.TimeoutError, Exception) as e:  # noqa: BLE001
            # 正文流式失败：若已有正文则 L3 兜底，否则 error
            text = "".join(answer_parts)
            block = await self._l3_keyword_fallback(text) if text else None
            if block is not None:
                yield {"kind": "sentinel"}
                yield {"kind": "json_done", "block": block}
            else:
                yield {"kind": "error", "message": f"L3 fallback miss ({e})"}
            return
        # 第二次：轻量抽取（可用约束解码）
        try:
            block = await asyncio.wait_for(
                self.backend.extract_only("".join(answer_parts)),
                timeout=settings.json_parse_timeout_s,
            )
            yield {"kind": "sentinel"}
            yield {"kind": "json_done", "block": block}
        except (asyncio.TimeoutError, Exception) as e:  # noqa: BLE001
            log.warning("L2 extract failed qa_id=%s: %s", self.qa_id, e)
            block = await self._l3_keyword_fallback("".join(answer_parts))
            if block is not None:
                yield {"kind": "sentinel"}
                yield {"kind": "json_done", "block": block}
            else:
                await self._trigger_backfill()
                yield {"kind": "error", "message": "extract failed (L1 via L2)"}

    async def _retry_extract_once(self, answer_text: str = "") -> Optional[ConceptBlock]:
        """重试一次结构化抽取（temperature 降 0.2，仅重抽概念块不重新生成正文）。

        answer_text：已流出的正文。此前传空串会让网关 400（空 user content）
        且模型无从抽取 -- 这是"层有正文但概念抽取失败"的直接根因之一。
        """
        try:
            block = await asyncio.wait_for(
                self.backend.extract_only(answer_text), timeout=settings.json_parse_timeout_s
            )
            return block
        except Exception as e:  # noqa: BLE001
            log.warning("retry extract failed qa_id=%s: %s", self.qa_id, e)
            return None

    async def _l3_keyword_fallback(self, text: str) -> Optional[ConceptBlock]:
        """L3：关键词匹配（预置概念表子串匹配）。"""
        if not text:
            return None
        try:
            block = await self.backend.extract_only(text)
            return block
        except Exception as e:  # noqa: BLE001
            log.warning("L3 keyword fallback miss qa_id=%s: %s", self.qa_id, e)
            return None

    async def _trigger_backfill(self) -> None:
        """L1 触发：落库待补标注任务。"""
        if self._backfill_hook is not None:
            try:
                await self._backfill_hook(self.qa_id)
            except Exception:  # noqa: BLE001
                log.exception("backfill_hook failed qa_id=%s", self.qa_id)
        else:
            log.info("L1 backfill queued (no hook) qa_id=%s", self.qa_id)


def build_client(qa_id: str, session_id: str, question: str, **kw) -> InferenceClient:
    """工厂：默认按环境配置选后端。"""
    return InferenceClient(qa_id, session_id, question, **kw)
