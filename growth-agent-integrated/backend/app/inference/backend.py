"""LLM 后端抽象（InferenceClient 与具体模型解耦）。

InferenceClient 依赖本协议而非具体 SDK，便于：
- 本地离线运行：StubLLMBackend 直接产出「流式正文 + sentinel + ConceptBlock」协议输出；
- 接入真实推理服务：实现 stream() / extract_only() 即可（OpenAI 兼容、自研网关等）。

每个后端绑定一个模型端点（熔断按端点维度统计）。
"""
from __future__ import annotations

import json
import logging
import os
from typing import AsyncIterator, Protocol, runtime_checkable

from app.config import settings
from app.schemas import ConceptBlock, ConceptItem

log = logging.getLogger(__name__)


@runtime_checkable
class LLMBackend(Protocol):
    """推理后端契约：流式正文 + 尾部结构化 JSON（sentinel 分割）。"""

    endpoint: str       # 模型端点标识（熔断按此维度统计）
    model: str

    async def stream(self, prompt: str) -> AsyncIterator[str]:
        """流式产出 token；正文与 sentinel + ConceptBlock JSON 连续吐出。"""
        ...

    async def extract_only(self, answer_text: str) -> ConceptBlock:
        """轻量抽取调用（L2 第二次 / 补标注）：从已有正文抽概念，不重生成正文。"""
        ...

    async def abort(self) -> None:
        """中断当前推理调用（用户回上层时 harness 调用）。"""
        ...


# ---------------------------------------------------------------------------
# StubLLMBackend：本地离线运行 / 测试用，产出符合协议的确定输出
# ---------------------------------------------------------------------------
class StubLLMBackend:
    """模拟一次成功的 L0 调用：先逐段吐正文，再吐 sentinel + ConceptBlock。

    可经 answer_chunks / concept_block / mode 注入不同降级场景（测试用）。
    mode:
      ok       -> 正文 + sentinel + 合法 JSON（L0）
      bad_json -> 正文 + sentinel + 损坏 JSON（L1）
      no_sentinel -> 只有正文，无 sentinel（L1）
      split    -> 只吐正文（模拟不支持并存，触发 L2 拆两次调用）
      error    -> 流式首 token 即抛（熔断重试用）
    """

    endpoint = "stub-endpoint"
    model = "stub-llm"

    def __init__(self, *, answer_chunks: list[str] | None = None,
                 concept_block: ConceptBlock | None = None,
                 mode: str = "ok"):
        self._answer_chunks = answer_chunks or [
            "关于这个问题，", "核心在于理解其基本概念。", "进一步展开来说……"
        ]
        self._concept_block = concept_block or ConceptBlock(
            concepts=[ConceptItem(name="概念A", aliases=["concept_a"], confidence=0.9)],
            model="stub-llm",
        )
        self.mode = mode

    async def stream(self, prompt: str) -> AsyncIterator[str]:
        for ch in self._answer_chunks:
            yield ch
        if self.mode == "no_sentinel":
            return
        if self.mode == "bad_json":
            yield f"\n{settings.concept_sentinel}\n" + "{not valid json"
            return
        if self.mode == "split":
            # 只吐正文，不吐 sentinel/JSON（触发 L2 拆两次调用）
            return
        yield f"\n{settings.concept_sentinel}\n"
        yield self._concept_block.model_dump_json()

    async def extract_only(self, answer_text: str) -> ConceptBlock:
        from app.inference.constraints import _heuristic_concepts
        if self.mode == "error":
            raise RuntimeError("extract_only failed (L3)")
        return ConceptBlock(concepts=_heuristic_concepts(answer_text), model=f"{self.model}#extract")

    async def abort(self) -> None:
        return None


# ---------------------------------------------------------------------------
# OpenAI 兼容后端（可选，配置后启用真实推理）
# ---------------------------------------------------------------------------
class OpenAICompatibleBackend:
    """OpenAI 兼容 Chat Completions 流式后端。

    环境变量：
      LLM_BASE_URL / LLM_API_KEY / LLM_MODEL / LLM_BACKUP_MODEL
    未配置时 InferenceClient 退化用 StubLLMBackend。
    """

    def __init__(self, base_url: str | None = None, api_key: str | None = None,
                 model: str | None = None):
        self.base_url = (base_url or os.getenv("LLM_BASE_URL") or "").rstrip("/")
        self.api_key = api_key or os.getenv("LLM_API_KEY", "")
        self.model = model or os.getenv("LLM_MODEL", "gpt-4o-mini")
        self.backup_model = os.getenv("LLM_BACKUP_MODEL", self.model)
        self.endpoint = f"{self.base_url}/chat/completions"
        self._aborted = False

    async def stream(self, prompt: str) -> AsyncIterator[str]:
        import httpx
        self._aborted = False
        payload = {
            "model": self.model, "stream": True,
            "messages": [{"role": "system", "content": "先输出正文，再换行输出 "
                f"{settings.concept_sentinel}，最后输出 ConceptBlock JSON。"},
                {"role": "user", "content": prompt}],
        }
        async with httpx.AsyncStream(
            self.endpoint, method="POST", headers={"Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json"}, json=payload, timeout=settings.inference_timeout_s,
        ) as resp:
            async for line in resp.aiter_lines():
                if self._aborted:
                    break
                if line.startswith("data: ") and line.strip() != "data: [DONE]":
                    try:
                        delta = json.loads(line[6:])["choices"][0]["delta"].get("content", "")
                        if delta:
                            yield delta
                    except (json.JSONDecodeError, KeyError, IndexError):
                        continue

    async def extract_only(self, answer_text: str) -> ConceptBlock:
        import httpx
        payload = {
            "model": self.model, "stream": False,
            "messages": [{"role": "system", "content": "从正文抽取概念，只输出 ConceptBlock JSON。"},
                {"role": "user", "content": answer_text}],
        }
        async with httpx.AsyncClient(timeout=settings.json_parse_timeout_s) as c:
            r = await c.post(self.endpoint, headers={"Authorization": f"Bearer {self.api_key}",
                "Content-Type": "application/json"}, json=payload)
            data = r.json()
        from app.inference.constraints import parse_concept_block
        block = parse_concept_block(data["choices"][0]["message"]["content"])
        if block is None:
            raise RuntimeError("extract_only produced unparseable block")
        return block

    async def abort(self) -> None:
        self._aborted = True


def default_backend() -> LLMBackend:
    """按环境配置选后端：配了 LLM_BASE_URL 用真实后端，否则 StubLLMBackend。"""
    if os.getenv("LLM_BASE_URL"):
        try:
            return OpenAICompatibleBackend()
        except Exception as e:  # 配置异常不阻塞，退化 stub
            log.warning("LLM backend init failed (%s), fallback to stub", e)
    return StubLLMBackend()
