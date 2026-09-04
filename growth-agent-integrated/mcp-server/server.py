"""starMindAgent MCP Server（stdio）。

把 sMA 后端 FastAPI 路由映射成 MCP tools，使 ZCode 等 agent 可以：
- import_material: 导入学习材料（markdown）建 L0 根 + 概念抽取
- ask: 提问（SSE 流收口成完整回答 + 概念列表）
- drilldown: 概念下钻
- learning_materials: 列出已导入材料
- concept_graph: 概念图（session 级 / 全局）
- session_tree: 会话 QA 步骤树（学到哪了）
- learning_profile: 学习画像

后端地址用环境变量 SMA_BACKEND 覆盖（默认 http://127.0.0.1:8000）。

ZCode 接入（工作区级，仓库根 .zcode/config.json，已配置）:
  mcp.servers.starmind = stdio server，command 用 python 绝对路径，
  args 指向本文件绝对路径（ZCode 不展开模板变量，必须绝对路径），
  timeoutMs=620000（ask 走 SSE 流最长 600s，必须盖过 ZCode 默认 30s）。
"""
from __future__ import annotations

import json
import os
import sys

import httpx
from mcp.server.mcpserver import MCPServer

SMA_BACKEND = os.getenv("SMA_BACKEND", "http://127.0.0.1:8000").rstrip("/")
DEFAULT_USER = os.getenv("SMA_USER_ID", "default")

mcp = MCPServer("starmind-agent")


def _concept_names(concepts: list) -> list[str]:
    """SSE concepts 事件里概念的展示名（字段是 canonical_name）。"""
    return [c.get("canonical_name") or c.get("name") or c.get("concept_id", "?")
            for c in concepts if isinstance(c, dict)]


def _http() -> httpx.AsyncClient:
    return httpx.AsyncClient(base_url=SMA_BACKEND, timeout=30.0)


class BackendError(Exception):
    """sMA 后端不可达 / 非 2xx。"""


async def _request(method: str, path: str, *, json_body: dict | None = None,
                   params: dict | None = None, timeout: float | None = None) -> dict:
    async with _http() as c:
        try:
            r = await c.request(method, path, json=json_body, params=params,
                                timeout=timeout or 30.0)
        except httpx.HTTPError as e:
            raise BackendError(f"sMA 后端不可达（{SMA_BACKEND}）: {e!r}") from e
    if r.status_code >= 400:
        raise BackendError(f"后端 {method} {path} -> {r.status_code}: {r.text[:300]}")
    return r.json() if r.content else {}


async def _consume_sse(path: str) -> dict:
    """消费一次 QA 的 SSE 流，收口成 {answer, concepts, search_sources, error}。

    事件契约见 app/qastep/state_machine.py:
      status / answer_delta / concept_candidates / search_sources / concepts / done / error
    """
    out = {"answer": "", "concepts": [], "search_sources": [], "status": None, "error": None}
    async with _http() as c:
        try:
            async with c.stream("GET", path, timeout=600.0) as r:
                if r.status_code >= 400:
                    body = (await r.aread()).decode(errors="ignore")
                    raise BackendError(f"后端 GET {path} -> {r.status_code}: {body[:300]}")
                buf = ""
                async for chunk in r.aiter_text():
                    buf += chunk
                    while "\n\n" in buf:
                        raw, buf = buf.split("\n\n", 1)
                        for line in raw.splitlines():
                            if not line.startswith("data: "):
                                continue
                            try:
                                ev = json.loads(line[6:])
                            except json.JSONDecodeError:
                                continue
                            t = ev.get("type")
                            if t == "answer_delta":
                                out["answer"] += ev.get("text", "")
                            elif t == "concepts":
                                out["concepts"] = ev.get("concepts", [])
                            elif t == "search_sources":
                                out["search_sources"] = ev.get("sources", [])
                            elif t == "status":
                                out["status"] = ev.get("status")
                            elif t == "error":
                                out["error"] = ev.get("message", "inference error")
        except httpx.HTTPError as e:
            out["error"] = f"SSE 连接中断: {e!r}"
    return out


# ---------------------------------------------------------------------------
# tools
# ---------------------------------------------------------------------------
@mcp.tool()
async def import_material(title: str, content: str, user_id: str = DEFAULT_USER) -> str:
    """导入一份 markdown 学习材料到 sMA（建 L0 根 QAStep + 自动抽取概念）。

    适合把代码库分析文档、技术笔记喂给学习系统。返回 material_id / qa_id / 概念数。

    Args:
        title: 材料标题（后续提问检索的锚点）
        content: markdown 全文
        user_id: 学习者标识，默认 "default"
    """
    out = await _request("POST", "/learning/import",
                         json_body={"user_id": user_id, "title": title, "content": content},
                         timeout=180.0)
    concepts = out.get("concepts") or []
    return json.dumps({
        "material_id": out.get("material_id"),
        "qa_id": out.get("qa_id"),
        "title": out.get("title"),
        "concept_count": len(concepts),
        "concepts": [c.get("name") or c.get("canonical_name") for c in concepts][:20],
    }, ensure_ascii=False)


@mcp.tool()
async def ask(question: str, user_id: str = DEFAULT_USER,
              material_id: str | None = None, domain_tag: str | None = None) -> str:
    """向 sMA 学习系统提一个问题，得到回答 + 该回答抽出的概念列表。

    若给 material_id，会先检索材料相关段落注入上下文（grounding 到已导入材料）。

    Args:
        question: 问题（中文最佳）
        user_id: 学习者标识
        material_id: 可选，关联已导入的学习材料
        domain_tag: 可选，领域标签
    """
    body: dict = {"question": question, "user_id": user_id}
    if material_id:
        body["material_id"] = material_id
    if domain_tag:
        body["domain_tag"] = domain_tag
    started = await _request("POST", "/qa/start", json_body=body)
    qa_id = started["qa_id"]
    out = await _consume_sse(f"/qa/{qa_id}/stream")
    result = {
        "qa_id": qa_id,
        "question": question,
        "answer": out["answer"],
        "concepts": out["concepts"],
        "concept_names": _concept_names(out["concepts"]),
    }
    if out["search_sources"]:
        result["search_sources"] = out["search_sources"]
    if out["error"]:
        result["error"] = out["error"]
    return json.dumps(result, ensure_ascii=False)


@mcp.tool()
async def drilldown(qa_id: str, concept_id: str, question: str | None = None,
                    mode: str | None = None) -> str:
    """对某个 QA 回答中的概念下钻：fork 子 QAStep 并生成更深层解释。

    Args:
        qa_id: 父层 QA 的 id（ask 的返回里有）
        concept_id: 要下钻的概念 id（concepts 列表里的 concept_id/id）
        question: 可选，自由问句（配合 mode="ask" 用完整问题）
        mode: "ask" = 用户针对性提问；默认走概念展开式包装
    """
    body: dict = {"concept_id": concept_id}
    if question:
        body["question"] = question
    if mode:
        body["mode"] = mode
    started = await _request("POST", f"/qa/{qa_id}/drilldown", json_body=body)
    child_id = started["qa_id"]
    out = await _consume_sse(f"/qa/{child_id}/stream")
    result = {
        "qa_id": child_id, "parent_qa_id": qa_id,
        "question": started.get("question") or question,
        "answer": out["answer"], "concepts": out["concepts"],
        "concept_names": _concept_names(out["concepts"]),
    }
    if out["error"]:
        result["error"] = out["error"]
    return json.dumps(result, ensure_ascii=False)


@mcp.tool()
async def learning_materials(user_id: str = DEFAULT_USER) -> str:
    """列出该学习者已导入的学习材料（material_id / 标题 / 大小 / 时间）。"""
    out = await _request("GET", "/learning/materials", params={"user_id": user_id})
    return json.dumps(out, ensure_ascii=False)


@mcp.tool()
async def concept_graph(session_id: str | None = None,
                        origin_filter: list[str] | None = None) -> str:
    """查 sMA 概念图：这个学习者已建立了哪些概念、概念间关系如何。

    session_id 给定查单次学习会话的图；不给查全局跨会话聚合图。

    Args:
        session_id: 可选，会话 id（session_tree 的返回里有）
        origin_filter: 可选，概念来源过滤: user_click / co_occurrence / domain_graph
    """
    if session_id:
        out = await _request("POST", "/concept/graph",
                             json_body={"session_id": session_id,
                                        "origin_filter": origin_filter})
    else:
        params = {"user_id": DEFAULT_USER}
        out = await _request("GET", "/concept/global", params=params)
    return json.dumps(out, ensure_ascii=False)


@mcp.tool()
async def session_tree(session_id: str) -> str:
    """查一次学习会话的完整 QA 步骤树（问过什么、下钻到哪、每步的回答摘要）。

    适合回答「这个学习者学到哪了」。session_id 来自 import_material 或 ask 后查
    learning_profile。
    """
    out = await _request("GET", f"/memory/sessions/{session_id}")
    steps = out.get("steps", [])
    for s in steps:
        if s.get("answer"):
            s["answer_preview"] = s["answer"][:200]
            del s["answer"]
    return json.dumps(out, ensure_ascii=False)


@mcp.tool()
async def learning_profile(user_id: str = DEFAULT_USER, refresh: bool = False) -> str:
    """读学习画像：已掌握概念、薄弱概念、兴趣方向、推荐。

    refresh=True 时先触发后端 LLM 重新总结画像（较慢）。

    Args:
        user_id: 学习者标识
        refresh: 是否先强制刷新画像
    """
    if refresh:
        await _request("POST", f"/memory/users/{user_id}/profile/refresh", timeout=120.0)
    out = await _request("GET", f"/memory/users/{user_id}/profile")
    return json.dumps(out, ensure_ascii=False)


@mcp.tool()
async def list_sessions(user_id: str = DEFAULT_USER) -> str:
    """列出学习者的所有学习会话（session_id / 领域 / 问题数 / 最近问题）。"""
    out = await _request("GET", f"/memory/users/{user_id}/sessions")
    return json.dumps(out, ensure_ascii=False)


@mcp.tool()
async def review_cards(user_id: str = DEFAULT_USER) -> str:
    """查记忆卡片复习概况：今日到期队列 + 学习进度（streak/归档数）。

    返回 progress（total/active/due_now/archived/streak_dist）和 due 队列
    （每张卡的 question / streak / due_at）。卡片连续 3 天自评「理解」才归档。

    Args:
        user_id: 学习者标识
    """
    progress = await _request("GET", f"/memory/cards/users/{user_id}/progress", timeout=30.0)
    due = await _request("GET", f"/memory/cards/users/{user_id}/due", timeout=30.0)
    # due 里答案先不给（盲 check 语义），只留问题
    if isinstance(due, list):
        for c in due:
            c.pop("answer", None)
            c.pop("source_answer", None)
    return json.dumps({"progress": progress, "due": due}, ensure_ascii=False)


@mcp.tool()
async def grade_card(card_id: str, grade: str, user_id: str = DEFAULT_USER) -> str:
    """给一张记忆卡片盲 check 评分（模拟学习者的第二天自评）。

    grade 三选一：
    - understood: 记住了（streak+1，连续 3 天归档）
    - forgot: 忘记了（streak 清零，明天再到期）
    - retry: 明天再试（streak 清零，明天再到期）

    Args:
        card_id: 卡片 id（review_cards 的 due 队列里有）
        grade: understood / forgot / retry
        user_id: 学习者标识
    """
    if grade not in ("understood", "forgot", "retry"):
        return json.dumps({"error": "grade must be understood/forgot/retry"}, ensure_ascii=False)
    out = await _request("POST", f"/memory/cards/{card_id}/grade",
                         json_body={"grade": grade}, timeout=30.0)
    return json.dumps(out, ensure_ascii=False)


if __name__ == "__main__":
    mcp.run()
