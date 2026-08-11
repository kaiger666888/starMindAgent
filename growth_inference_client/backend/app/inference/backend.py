"""LLM 后端抽象 + OpenAI 兼容流式实现。

InferenceClient 通过 LLMBackend Protocol 解耦具体推理引擎：
- OpenAICompatibleBackend：标准 OpenAI /v1/chat/completions 流式 + guided JSON；
- 测试用 FakeLLMBackend：按预设 chunk 列表产出，无需网络。

生产环境可替换为自托管 vLLM / TensorRT-LLM 端点（同样走 OpenAI 兼容协议），
约束解码通过 guided_json 参数启用（协议 4.1）。
"""
from __future__ import annotations

import json
import logging
from typing import Any, AsyncIterator, Optional, Protocol

log = logging.getLogger(__name__)


class LLMBackend(Protocol):
    """推理后端契约。"""

    async def stream(
        self,
        messages: list[dict],
        *,
        model: str,
        temperature: float = 0.7,
        max_tokens: int = 2048,
        guided: Optional[dict] = None,
        timeout: float = 60.0,
    ) -> AsyncIterator[str]:
        """流式产出 token 文本（已拼好的 delta content）。"""
        ...

    async def complete(
        self,
        messages: list[dict],
        *,
        model: str,
        temperature: float = 0.7,
        max_tokens: int = 1024,
        guided: Optional[dict] = None,
        timeout: float = 30.0,
    ) -> str:
        """一次性返回完整文本。"""
        ...


class OpenAICompatibleBackend:
    """OpenAI 兼容 /v1/chat/completions 后端（httpx 流式 SSE）。"""

    def __init__(self, base_url: str, api_key: str, client=None):
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        # 复用外部 httpx.AsyncClient 以便连接池
        self._client = client

    async def _get_client(self):
        if self._client is not None:
            return self._client
        import httpx

        self._client = httpx.AsyncClient(timeout=httpx.Timeout(60.0, connect=5.0))
        return self._client

    def _build_payload(
        self, messages, model, temperature, max_tokens, guided, stream
    ) -> dict:
        payload: dict[str, Any] = {
            "model": model,
            "messages": messages,
            "temperature": temperature,
            "max_tokens": max_tokens,
            "stream": stream,
        }
        if guided:
            # vLLM guided_json 优先；同时带 response_format 兼容 OpenAI json_schema
            if "guided_json" in guided:
                payload["guided_json"] = guided["guided_json"]
            if "response_format" in guided:
                payload["response_format"] = guided["response_format"]
        return payload

    async def stream(
        self,
        messages: list[dict],
        *,
        model: str,
        temperature: float = 0.7,
        max_tokens: int = 2048,
        guided: Optional[dict] = None,
        timeout: float = 60.0,
    ) -> AsyncIterator[str]:
        import httpx

        payload = self._build_payload(messages, model, temperature, max_tokens, guided, stream=True)
        client = await self._get_client()
        url = f"{self.base_url}/chat/completions"
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }
        async with client.stream(
            "POST", url, json=payload, headers=headers, timeout=timeout
        ) as resp:
            if resp.status_code != 200:
                body = await resp.aread()
                raise RuntimeError(
                    f"LLM stream HTTP {resp.status_code}: {body[:500]!r}"
                )
            async for line in resp.aiter_lines():
                if not line or not line.startswith("data:"):
                    continue
                data = line[len("data:"):].strip()
                if data == "[DONE]":
                    break
                try:
                    obj = json.loads(data)
                except json.JSONDecodeError:
                    continue
                choices = obj.get("choices") or []
                if not choices:
                    continue
                delta = choices[0].get("delta") or {}
                content = delta.get("content")
                if content:
                    yield content

    async def complete(
        self,
        messages: list[dict],
        *,
        model: str,
        temperature: float = 0.7,
        max_tokens: int = 1024,
        guided: Optional[dict] = None,
        timeout: float = 30.0,
    ) -> str:
        payload = self._build_payload(messages, model, temperature, max_tokens, guided, stream=False)
        client = await self._get_client()
        url = f"{self.base_url}/chat/completions"
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }
        resp = await client.post(url, json=payload, headers=headers, timeout=timeout)
        if resp.status_code != 200:
            raise RuntimeError(f"LLM complete HTTP {resp.status_code}: {resp.text[:500]!r}")
        obj = resp.json()
        choices = obj.get("choices") or []
        if not choices:
            return ""
        return (choices[0].get("message") or {}).get("content", "") or ""


class FakeLLMBackend:
    """测试用桩后端：按预设 chunk 列表产出，支持 guided 标记校验。

    chunks: 主调用的流式 chunk 列表（拼接后即完整输出）。
    complete_text: complete() 调用返回的文本（用于重试 / L2 第二次抽取）。
    """

    def __init__(
        self,
        chunks: Optional[list[str]] = None,
        complete_text: str = "",
        complete_texts: Optional[list[str]] = None,
        stream_error: Optional[Exception] = None,
    ):
        self._chunks = chunks or []
        self._complete_text = complete_text
        # 支持多次 complete 调用依次返回不同结果（重试场景）
        self._complete_texts = list(complete_texts) if complete_texts else []
        self.stream_error = stream_error
        self.last_guided: Optional[dict] = None
        self.last_messages: Optional[list[dict]] = None
        self.call_count = 0

    async def stream(self, messages, *, model, temperature=0.7, max_tokens=2048,
                     guided=None, timeout=60.0) -> AsyncIterator[str]:
        self.last_messages = messages
        self.last_guided = guided
        if self.stream_error is not None:
            raise self.stream_error
        for ch in self._chunks:
            yield ch

    async def complete(self, messages, *, model, temperature=0.7, max_tokens=1024,
                       guided=None, timeout=30.0) -> str:
        self.last_messages = messages
        self.last_guided = guided
        self.call_count += 1
        if self._complete_texts:
            return self._complete_texts.pop(0)
        return self._complete_text
