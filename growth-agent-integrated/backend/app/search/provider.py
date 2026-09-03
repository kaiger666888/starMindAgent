"""联网搜索 Provider（SearXNG 本地容器，免费无限量）。

实测（2026-08-31，本机网络）：
- SearXNG 容器 127.0.0.1:8888，brave + google cse 聚合正常
- 中文概念/中英时效查询 1.7~4.4s，结果带 content 摘要（可直接进 prompt）
- 默认配置禁 JSON API，容器 settings.yml 已开 formats: [html, json]

设计：SearchProvider 协议 + SearXNGSearchProvider 实现。
后续接 Wikipedia/Tavily 等新源只需加实现类，按 SEARCH_PROVIDER env 路由。
"""
from __future__ import annotations

import asyncio
import logging
import os
from dataclasses import dataclass
from typing import Protocol

log = logging.getLogger(__name__)


@dataclass
class SearchHit:
    """单条搜索结果：标题/URL/摘要。"""
    title: str
    url: str
    content: str


class SearchProvider(Protocol):
    """搜索源契约：query -> 摘要文本（拼 LLM prompt 用）+ 命中列表。"""

    async def search(self, query: str, language: str = "auto") -> tuple[str, list[SearchHit]]:
        ...


class SearXNGSearchProvider:
    """SearXNG JSON API 实现。

    环境变量：
      SEARXNG_URL（默认 http://127.0.0.1:8888）
    返回 (prompt_text, hits)：prompt_text 是拼进 role=tool 消息的
    编号列表（每条截断 ~300 字），hits 供前端来源块展示。
    """

    def __init__(self, base_url: str | None = None, timeout: float = 10.0):
        self.base_url = (base_url or os.getenv("SEARXNG_URL", "http://127.0.0.1:8888")).rstrip("/")
        self.timeout = timeout

    async def search(self, query: str, language: str = "auto") -> tuple[str, list[SearchHit]]:
        import httpx
        try:
            async with httpx.AsyncClient(timeout=self.timeout) as c:
                r = await c.get(f"{self.base_url}/search", params={
                    "q": query, "format": "json",
                    "language": language, "safesearch": 0,
                })
                if r.status_code != 200:
                    log.warning("searxng %s -> %s: %s", query[:30],
                                r.status_code, r.text[:120])
                    return "", []
                results = r.json().get("results", [])[:6]
        except Exception as e:  # noqa: BLE001  搜索失败不阻断主流程
            log.warning("searxng search failed (%s): %r", query[:30], e)
            return "", []
        hits = [
            SearchHit(title=(x.get("title") or "")[:80],
                      url=x.get("url") or "",
                      content=(x.get("content") or "")[:300])
            for x in results if x.get("url")
        ]
        if not hits:
            return "", []
        lines = [f"{i+1}. {h.title}\n   {h.url}\n   {h.content}"
                 for i, h in enumerate(hits)]
        prompt_text = f"搜索「{query}」的结果：\n" + "\n".join(lines)
        return prompt_text, hits


# ---------------------------------------------------------------------------
# 路由 + 并行多查询执行（tools 轮询到的多个 query 一起搜，省时）
# ---------------------------------------------------------------------------
def default_search_provider() -> SearchProvider | None:
    """按环境配置选搜索源；未启用返回 None（调用方跳过搜索）。"""
    if os.getenv("SEARCH_ENABLED", "1").lower() not in ("1", "true", "enabled", "on"):
        return None
    kind = os.getenv("SEARCH_PROVIDER", "searxng").lower()
    if kind == "searxng":
        return SearXNGSearchProvider()
    log.warning("unknown SEARCH_PROVIDER %s, search disabled", kind)
    return None


async def run_searches(queries: list[str], language: str = "auto",
                       max_queries: int = 3) -> tuple[str, list[dict]]:
    """并行执行多个搜索 query，返回 (合并 prompt 文本, 前端来源列表)。

    max_queries 硬上限：模型一次最多吐几个 query 都只执行前 N 个
    （防搜索放大：每问 1 轮、最多 3 个 query、每个 6 条结果）。
    单个 query 失败不影响其余（gather return_exceptions）。
    """
    provider = default_search_provider()
    if provider is None or not queries:
        return "", []
    queries = queries[:max_queries]
    results = await asyncio.gather(
        *(provider.search(q, language) for q in queries),
        return_exceptions=True,
    )
    prompt_parts: list[str] = []
    sources: list[dict] = []
    for q, r in zip(queries, results):
        if isinstance(r, Exception) or not r:
            continue
        text, hits = r
        if text:
            prompt_parts.append(text)
        sources.extend({
            "query": q, "title": h.title, "url": h.url, "snippet": h.content,
        } for h in hits[:3])  # 前端来源块每 query 最多 3 条
    return "\n\n".join(prompt_parts), sources
