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


_shared_http = None


def _shared_client():
    """进程级 httpx.AsyncClient（连接池保活）。

    每请求新建 AsyncClient 要付 TCP+TLS 握手（https 网关 ~0.3s）；
    共享 client 复用连接，同端点后续请求免握手。timeout 逐调用覆盖
    （httpx 请求级 timeout 优先于 client 级），这里 client 级不设。
    uvicorn 单事件循环，跨协程共享安全。
    """
    global _shared_http
    if _shared_http is None:
        import httpx
        _shared_http = httpx.AsyncClient(
            limits=httpx.Limits(max_connections=20, max_keepalive_connections=10),
        )
    return _shared_http


class _GatewayOverload(Exception):
    """网关过载/OOM（5xx、Error code 1210、Out of Memory）：可退避重试。"""


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


def _concept_system_prompt(model: str = "llm") -> str:
    """sentinel 协议 system 提示（含 ConceptBlock schema）。

    不给 schema 时 glm 会自由发挥成 {"ms": [...], "one_line": ...} 之类的
    自定义结构 -> ConceptBlock 校验必败 -> 每次 L1 降级（retry/fallback
    两次 60s 超时调用，全链路拖 3-4 分钟）。
    """
    return (
        "先输出自然语言正文，然后另起一行输出 "
        f"{settings.concept_sentinel}，最后输出符合以下 schema 的 JSON"
        "（裸 JSON，不要 markdown 代码块）：\n"
        '{"concepts": [{"name": "概念规范名", "aliases": ["别名"], '
        f'"confidence": 0.9}}, ...], "model": "{model}"}}\n'
        "name 为 2-8 字核心概念名，抽 3-8 个；aliases 含中英文/缩写；"
        "confidence 取 0-1。正文里禁止出现 sentinel 标记。"
        # 代码库 grounding 引导（CODEBASE_DIR 配置时 tools 轮才带 code_query）。
        # 放 web_search 之前：glm 对工具调用的触发判断受 system 尾部指令稀释，
        # "学习助手"人设语境下倾向直答，代码类问题必须强指令（E2E 实测定位）。
        "若提供了 code_query 工具：只要问题中出现具体类名/函数名/文件名，或问"
        "「代码库里如何实现/定义在哪」，必须先调用它检索真实代码片段再回答，"
        "禁止凭通用知识编造代码库实现；回答时指明片段所在文件。仅纯理论/通用"
        "概念问题直答。"
        # 联网搜索引导（tools 轮才生效，无 tools 时模型忽略此段）
        "若提供了 web_search 工具：仅在问题涉及时效性信息（最新版本/近期发布/"
        "新闻/当前状态）或你不确定的事实时调用它，经典概念与原理类问题直接回答。"
        "基于搜索结果回答时自然融入信息，并在正文提及关键出处名。"
    )


# 联网搜索工具定义（OpenAI function calling）。
# 触发策略由 system prompt 引导模型自判：时效性问题才搜，经典概念直答。
_WEB_SEARCH_TOOL = {
    "type": "function",
    "function": {
        "name": "web_search",
        "description": "搜索互联网获取最新信息。仅当问题涉及时效性内容（最新版本、近期发布、新闻、当前状态）或你不确定的事实时调用；经典概念、原理、机制类问题不要调用。",
        "parameters": {
            "type": "object",
            "properties": {"query": {"type": "string", "description": "搜索关键词，精炼到 10 字内"}},
            "required": ["query"],
        },
    },
}

# 代码库 grounding 工具（rg 执行层在 app/search/codebase.py）。
# CODEBASE_DIR 未配置时不进 tools 列表，链路零影响。
_CODE_QUERY_TOOL = {
    "type": "function",
    "function": {
        "name": "code_query",
        "description": "在已配置的代码库中检索真实代码片段（ripgrep）。规则：只要问题中出现具体类名/函数名/文件名/模块名，或出现「代码库」「源码」「实现」「定义在哪」等字样，就必须先调用本工具拿到真实代码再回答，禁止凭通用知识猜测或编造代码库的实现细节。仅纯理论/通用概念问题（完全不涉及具体代码）才不调用。",
        "parameters": {
            "type": "object",
            "properties": {"query": {"type": "string", "description": "代码检索词：类名/函数名/标识符/关键词，10 字内"}},
            "required": ["query"],
        },
    },
}


def tool_calls_raw(calls: list[dict]) -> list[dict]:
    """聚合后的 tool_calls 转回 OpenAI 原始格式（role=tool 回填用）。

    聚合格式存的是 arguments_json（含我们拼的键名）；回填网关要
    标准的 function.arguments 字符串。
    """
    return [{
        "id": c["id"], "type": "function",
        "function": {"name": c["function"]["name"],
                     "arguments": c["function"]["arguments_json"]},
    } for c in calls]


def _code_terms(prompt: str, max_terms: int = 2) -> list[str]:
    """从 prompt 抽代码标识符特征词（确定性预检索用）。

    命中模式（prompt 尾部优先，SYSTEM/链摘要是中文不会误命中）：
    - 驼峰标识符：QAStepPipeline / OpenAICompatibleBackend
    - 下划线标识符：state_machine / run_code_search（≥2 段）
    - 「X」引号词：下钻问题的概念名（查不到就空，零伤害）
    短词（≤3 字符）与常见英文虚词过滤掉。
    """
    import re as _re
    seen: list[str] = []
    tail = prompt[-600:]  # question 在 blob 末端，只扫尾部
    pats = [
        # CamelCase（首段支持连续大写开头：QA/LLM/SMA）
        r"(?:[A-Z]{2,}[a-z0-9]*|[A-Z][a-z0-9]+)(?:[A-Z][a-z0-9]+)+",
        r"[a-z]{2,}(?:_[a-z0-9]{2,})+",              # snake_case
        r"「([^」]{2,20})」",                          # 引号概念词
    ]
    for p in pats:
        for m in _re.findall(p, tail):
            t = m if isinstance(m, str) else str(m)
            t = t.strip()
            if len(t) >= 4 and t.lower() not in ("test", "json", "http", "none", "true", "false") \
                    and t not in seen:
                seen.append(t)
    return seen[:max_terms]


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
        # 长连接复用：模块级 client 池（每请求新建 AsyncClient 要付 TCP+TLS
        # 握手 ~0.3s；连接池保活后同端点请求免握手，TTFT 直接受益）
        self._http = _shared_client()
        # 思考模型（如 glm-5.2）默认关闭思考以避免 30-50s 首 token 延迟
        # 设 LLM_THINKING=enabled 可恢复思考（深度优先）
        self.thinking_enabled = os.getenv("LLM_THINKING", "disabled").lower() in ("1", "true", "enabled", "on")

    def _payload(self, messages, stream: bool) -> dict:
        payload = {"model": self.model, "stream": stream, "messages": messages}
        if not self.thinking_enabled:
            # 关闭思考：网关直接吐 content，首 token 秒回
            payload["thinking"] = {"type": "disabled"}
        return payload

    def _concept_system(self) -> str:
        return _concept_system_prompt(self.model or "llm")

    async def stream(self, prompt: str) -> AsyncIterator[str]:
        """两轮 agentic 流式（联网搜索按需触发）。

        第一轮：带 web_search tools 定义流式调用--
          - 经典概念：模型直接吐正文 -> 透传（零额外开销，流式体验不变）
          - 时效性问题：模型返回 tool_calls -> 聚合 query，执行搜索，
            把结果以 role=tool 消息回填，进第二轮
        第二轮：流式产出正文 + sentinel 概念 JSON（协议不变）。

        触发策略三层（SEARCH_ENABLED 总开关 / 模型自判 / 每问最多 1 轮
        3 query 硬上限，见 app/search/provider.py run_searches）。
        搜索来源存 self.last_search_sources 供 pipeline 发 SSE 来源事件。
        """
        import httpx
        self._aborted = False
        self.last_search_sources: list[dict] = []
        from app.search.provider import run_searches, default_search_provider
        from app.search.codebase import run_code_search, codebase_root
        search_on = default_search_provider() is not None
        code_on = codebase_root() is not None
        # 打桩：工具挂载状态（code grounding 排障用，grep "agent tools"）
        log.info("agent tools: web_search=%s code_query=%s (root=%s)",
                 search_on, code_on, codebase_root() or "-")

        messages = [{"role": "system", "content": self._concept_system()},
            {"role": "user", "content": prompt}]
        # 确定性代码预检索：问题含代码标识符特征时直接 rg 注入，不赌模型自判。
        # 实测 glm 对「自以为知道」的代码问题不触发 tool_calls（模型幻觉模式下
        # 编造实现细节），web_search 能触发是因为时效性问题模型知道自己不知道。
        # 特征词从 prompt 尾部抽（question 在 blob 末端）：驼峰/下划线标识符。
        if code_on:
            terms = _code_terms(prompt)
            if terms:
                pre_text, _ = await run_code_search(terms)
                if pre_text:
                    messages[1]["content"] = (
                        "【代码库检索结果】（与问题相关的真实代码片段，"
                        "回答时优先依据这些片段并指明文件，禁止编造）\n"
                        + pre_text[:3000] + "\n\n【原问题】\n" + prompt)
        if search_on or code_on:
            # 第一轮：流式 + tools（乐观直吐正文）
            async for tok in self._stream_round(messages, expect_tools=True,
                                               tools=self._round_tools(search_on, code_on)):
                yield tok
            if self._round1_tool_calls:
                # 分流 tool_calls：web_search -> run_searches，code_query -> run_code_search。
                # 不走 role=tool 回填：实测网关对 tool 结果消息的续写不稳定
                # （间歇性 content="" finish=stop 空响应，约 50%），而 prompt
                # 注入路径稳定（与学习材料 material_context 同构）。
                # arguments_json 是 '{"query": "..."}' 原始串，须解析出 query 值。
                search_qs: list[str] = []
                code_qs: list[str] = []
                for c in self._round1_tool_calls:
                    try:
                        q = json.loads(c["function"]["arguments_json"]).get("query", "")
                    except (json.JSONDecodeError, AttributeError):
                        continue
                    if not q:
                        continue
                    if c["function"]["name"] == "code_query":
                        code_qs.append(q)
                    else:
                        search_qs.append(q)
                # 两类工具并行执行（互不等待）
                search_text, sources = (await run_searches(search_qs)
                                        if search_qs else ("", []))
                self.last_search_sources = sources
                code_text, _ = (await run_code_search(code_qs)
                                if code_qs else ("", []))
                messages2 = [messages[0], dict(messages[1])]  # system + user 副本
                sections: list[str] = []
                if code_text:
                    sections.append(
                        "【代码库检索结果】（基于这些真实代码片段回答，"
                        "自然指明片段所在文件，不要编造）\n" + code_text[:3000])
                if search_text:
                    sections.append(
                        "【联网搜索结果】（基于这些最新信息回答，"
                        "自然融入正文并提及关键出处名）\n" + search_text[:3000])
                if sections:
                    messages2[1]["content"] = ("\n\n".join(sections)
                                              + "\n\n【原问题】\n" + messages[1]["content"])
                else:
                    messages2[1]["content"] = (
                        "（检索无结果，请基于已有知识回答并说明可能不全面）\n\n"
                        + messages[1]["content"])
                async for tok in self._stream_plain(messages2):
                    yield tok
            return
        # 全部开关关闭：常规单轮流式
        async for tok in self._stream_plain(messages):
            yield tok
        return

    @staticmethod
    def _round_tools(search_on: bool, code_on: bool) -> list[dict]:
        """第一轮挂载的工具列表（按开关拼装）。"""
        tools = []
        if search_on:
            tools.append(_WEB_SEARCH_TOOL)
        if code_on:
            tools.append(_CODE_QUERY_TOOL)
        return tools

    async def _stream_round(self, messages: list, expect_tools: bool,
                            tools: list[dict] | None = None) -> AsyncIterator[str]:
        """第一轮流式（乐观直吐）：正文 delta 直接 yield，直答场景全程流式
        零损耗；同时聚合 tool_calls 增量到 self._round1_tool_calls。模型需要
        搜索时 content 为空直接吐 tool_calls（实测），几乎无收回场景。"""
        self._round1_tool_calls = None
        self._prologue = []
        agg = {}
        payload = self._payload(messages, stream=True)
        if expect_tools:
            payload["tools"] = tools or [_WEB_SEARCH_TOOL]
            payload["tool_choice"] = "auto"
        async with self._http.stream("POST", self.endpoint,
                headers={"Authorization": f"Bearer {self.api_key}",
                "Content-Type": "application/json"}, json=payload,
                timeout=settings.inference_timeout_s) as resp:
            async for line in resp.aiter_lines():
                if self._aborted:
                    break
                if not (line.startswith("data: ") and line.strip() != "data: [DONE]"):
                    continue
                try:
                    chunk = json.loads(line[6:])["choices"][0]
                except (json.JSONDecodeError, KeyError, IndexError):
                    continue
                delta = chunk.get("delta", {})
                if delta.get("content"):
                    self._prologue.append(delta["content"])
                    yield delta["content"]
                for tc in delta.get("tool_calls") or []:
                    idx = tc.get("index", 0)
                    slot = agg.setdefault(idx, {
                        "id": tc.get("id") or "", "type": "function",
                        "function": {"name": "", "arguments_json": ""},
                    })
                    if tc.get("id"):
                        slot["id"] = tc["id"]
                    fn = tc.get("function") or {}
                    if fn.get("name"):
                        slot["function"]["name"] = fn["name"]
                    if fn.get("arguments"):
                        slot["function"]["arguments_json"] += fn["arguments"]
        if agg:
            calls = [agg[i] for i in sorted(agg)]
            calls = [c for c in calls if self._safe_query(c)]
            if calls:
                if self._prologue:
                    log.warning("round1 had both content and tool_calls (%d chars lost)", len("".join(self._prologue)))
                self._round1_tool_calls = calls

    def _safe_query(self, call: dict) -> bool:
        """校验聚合的 tool_call：函数名在白名单 + arguments 可解析出非空 query。"""
        import json as _json
        if call["function"]["name"] not in ("web_search", "code_query"):
            return False
        try:
            args = _json.loads(call["function"]["arguments_json"] or "{}")
            return bool(args.get("query"))
        except (_json.JSONDecodeError, AttributeError):
            return False

    async def _stream_plain(self, messages: list) -> AsyncIterator[str]:
        """纯流式（无 tools）：搜索场景是 tool 结果回填后的第二轮最终生成。"""
        payload = self._payload(messages, stream=True)
        async with self._http.stream("POST", self.endpoint,
                headers={"Authorization": f"Bearer {self.api_key}",
                "Content-Type": "application/json"}, json=payload,
                timeout=settings.inference_timeout_s) as resp:
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
        messages = [{"role": "system", "content": "从正文抽取概念，只输出 ConceptBlock JSON。"},
            {"role": "user", "content": answer_text}]
        payload = self._payload(messages, stream=False)
        r = await self._http.post(self.endpoint, headers={"Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json"}, json=payload,
            timeout=settings.json_parse_timeout_s)
        data = r.json()
        from app.inference.constraints import parse_concept_block
        block = parse_concept_block(data["choices"][0]["message"]["content"])
        if block is None:
            raise RuntimeError("extract_only produced unparseable block")
        return block

    async def complete_text(self, system: str, user: str, timeout: float = 30.0) -> str:
        """通用非流式文本生成（层摘要/画像总结等复用）。返回 message.content。"""
        import httpx
        payload = self._payload(
            [{"role": "system", "content": system}, {"role": "user", "content": user}],
            stream=False,
        )
        r = await self._http.post(self.endpoint,
            headers={"Authorization": f"Bearer {self.api_key}", "Content-Type": "application/json"},
            json=payload, timeout=timeout)
        data = r.json()
        return data["choices"][0]["message"].get("content") or ""

    async def abort(self) -> None:
        self._aborted = True


def default_backend() -> LLMBackend:
    """按环境配置选后端：
    - LLM_BACKEND=anthropic 或配了 ANTHROPIC_BASE_URL → AnthropicBackend（走 gateway）
    - 配了 LLM_BASE_URL → OpenAICompatibleBackend
    - 否则 StubLLMBackend
    """
    backend_kind = os.getenv("LLM_BACKEND", "").lower()
    # LLM_BACKEND=openai 显式路由（最高优先）：Anthropic 全局环境变量存在时
    # 切到 OpenAI 协议网关（higress /v1/chat/completions，thinking 可关、
    # TTFT ~2s，实测 2026-08-27 比 ai-nexus 的 Anthropic 通道快一个量级）
    if backend_kind == "openai":
        try:
            return OpenAICompatibleBackend()
        except Exception as e:  # 配置异常不阻塞，退化 stub
            log.warning("OpenAI backend init failed (%s), fallback to stub", e)
            return StubLLMBackend()
    if backend_kind == "anthropic" or os.getenv("ANTHROPIC_BASE_URL"):
        try:
            return AnthropicBackend()
        except Exception as e:
            log.warning("Anthropic backend init failed (%s), fallback", e)
    if os.getenv("LLM_BASE_URL"):
        try:
            return OpenAICompatibleBackend()
        except Exception as e:  # 配置异常不阻塞，退化 stub
            log.warning("LLM backend init failed (%s), fallback to stub", e)
    return StubLLMBackend()


class AnthropicBackend:
    """Anthropic 协议后端（走 .claude 的 higress gateway）。

    环境变量：
      ANTHROPIC_BASE_URL / ANTHROPIC_API_KEY / ANTHROPIC_MODEL
    gateway 是 Anthropic 协议(/v1/messages + x-api-key)，与 OpenAI 协议不同。
    流式 SSE 事件: content_block_delta { delta: { text } }。
    """
    def __init__(self, base_url: str | None = None, api_key: str | None = None,
                 model: str | None = None):
        self.base_url = (base_url or os.getenv("ANTHROPIC_BASE_URL") or "").rstrip("/")
        self.api_key = api_key or os.getenv("ANTHROPIC_API_KEY", "")
        self.model = model or os.getenv("ANTHROPIC_MODEL", "glm")
        self.endpoint = f"{self.base_url}/v1/messages"
        self._aborted = False
        # 复用 thinking 开关
        self.thinking_enabled = os.getenv("LLM_THINKING", "disabled").lower() in ("1", "true", "enabled", "on")

    def _concept_system(self) -> str:
        return _concept_system_prompt(self.model or "llm")

    def _headers(self):
        return {
            "x-api-key": self.api_key,
            "anthropic-version": "2023-06-01",
            "Content-Type": "application/json",
        }

    def _payload(self, messages, stream: bool, max_tokens: int | None = None) -> dict:
        # Anthropic 格式: system 单独字段, messages 只含 user/assistant
        #
        # -- 低内存推理策略（实测 2026-08-24）--
        # 网关 glm-5.3 默认强制 thinking 且不限量：不传 thinking 参数时
        # thinking 会吃光全部 max_tokens（stop=max_tokens、正文 0 字），
        # 长思考也是网关推理实例 OOM（"Error code: 1210 - Out of Memory"）
        # 的主要压力源。实测网关尊重两种显式控制：
        #   thinking disabled          -> 秒回、正文完整、output_tokens 小
        #   thinking budget_tokens=N   -> 思考被卡在 N 内
        # 故默认 disabled（探索问答/抽取不需要深度思考）；LLM_THINKING=enabled
        # 时带 budget_tokens 护栏（防思考无限膨胀拉高内存）。
        # max_tokens 默认 2000：正文限 500 字 + sentinel JSON，2K 绰绰有余。
        # 8K 预算只会推高网关内存峰值（OOM 1210 风险）不产生额外价值。
        # 长 prompt 场景（材料精判）由调用方显式覆盖（complete_text max_tokens）。
        if max_tokens is None:
            max_tokens = int(os.getenv("LLM_MAX_TOKENS", "2000"))
        system = ""
        user_msgs = []
        for m in messages:
            if m["role"] == "system":
                system += m["content"] + "\n"
            else:
                user_msgs.append(m)
        payload = {
            "model": self.model,
            "stream": stream,
            "max_tokens": max_tokens,
            "messages": user_msgs,
        }
        if self.thinking_enabled:
            budget = int(os.getenv("LLM_THINKING_BUDGET", "3000"))
            budget = min(budget, max(1024, max_tokens // 3))
            payload["thinking"] = {"type": "enabled", "budget_tokens": budget}
        else:
            payload["thinking"] = {"type": "disabled"}
        if system.strip():
            payload["system"] = system.strip()
        return payload

    async def stream(self, prompt: str) -> AsyncIterator[str]:
        import httpx
        self._aborted = False
        messages = [
            {"role": "system", "content": self._concept_system()},
            {"role": "user", "content": prompt},
        ]
        payload = self._payload(messages, stream=True)
        # 网关 OOM/过载（如 "Error code: 1210 - Out of Memory"）退避重试一次：
        # 瞬时故障重试即可恢复；重试仍失败给友好文案，不让原始错误码裸奔到前端。
        for attempt in range(2):
            try:
                async for tok in self._stream_once(payload):
                    yield tok
                return
            except _GatewayOverload as e:
                if attempt == 0:
                    import asyncio
                    log.warning("gateway overloaded (%s), retrying after 2s "
                                "model=%s", e, self.model)
                    await asyncio.sleep(2)
                else:
                    raise RuntimeError(
                        "推理服务当前繁忙（网关过载），请稍后重试"
                    ) from e

    async def _stream_once(self, payload: dict) -> AsyncIterator[str]:
        import httpx
        async with httpx.AsyncClient(timeout=settings.inference_timeout_s) as client:
            async with client.stream("POST", self.endpoint,
                headers=self._headers(), json=payload) as resp:
                if resp.status_code != 200:
                    body = (await resp.aread()).decode(errors="replace")[:500]
                    log.error("gateway %s -> %s: %s", self.endpoint,
                              resp.status_code, body)
                    if resp.status_code >= 500 or "Out of Memory" in body or "1210" in body:
                        raise _GatewayOverload(f"{resp.status_code}: {body[:200]}")
                    raise RuntimeError(f"gateway {resp.status_code}: {body}")
                stop_reason = None
                async for line in resp.aiter_lines():
                    if self._aborted:
                        break
                    # Anthropic SSE: "event: content_block_delta" 后跟 "data: {...}"
                    if line.startswith("data: "):
                        try:
                            ev = json.loads(line[6:])
                            # 只取 text delta（跳过 thinking）
                            if ev.get("type") == "content_block_delta":
                                delta = ev.get("delta", {})
                                if delta.get("type") == "text_delta":
                                    text = delta.get("text", "")
                                    if text:
                                        yield text
                            elif ev.get("type") == "error":
                                log.error("gateway SSE error event: %s",
                                          json.dumps(ev, ensure_ascii=False)[:500])
                            elif ev.get("type") == "message_delta":
                                stop_reason = ev.get("delta", {}).get("stop_reason")
                        except (json.JSONDecodeError, KeyError, IndexError):
                            continue
                # 思考吃光 max_tokens 预算 -> 正文缺失（网关强制思考模式下发生过）
                if stop_reason == "max_tokens":
                    log.warning("gateway stop_reason=max_tokens "
                                "(thinking 可能挤占正文预算) model=%s", self.model)

    async def extract_only(self, answer_text: str) -> ConceptBlock:
        # 复用 complete_text 拿文本，再 parse
        raw = await self.complete_text("从正文抽取概念，只输出 ConceptBlock JSON。", answer_text)
        from app.inference.constraints import parse_concept_block
        block = parse_concept_block(raw)
        if block is None:
            raise RuntimeError("extract_only produced unparseable block")
        return block

    async def complete_text(self, system: str, user: str, timeout: float = 60.0,
                            max_tokens: int | None = None) -> str:
        """非流式：调 /v1/messages，返回 content[0].text（跳过 thinking）。

        网关 OOM/过载退避重试一次（抽取/摘要类调用短平快，重试代价低）。
        max_tokens 可覆盖（长 prompt 会触发网关 thinking leak：思考吃光
        默认 8000 预算、正文 0 字，提到 16000 让正文有机会出来）。
        """
        import httpx
        payload = self._payload(
            [{"role": "system", "content": system}, {"role": "user", "content": user}],
            stream=False, max_tokens=max_tokens,
        )
        for attempt in range(2):
            async with httpx.AsyncClient(timeout=timeout) as c:
                r = await c.post(self.endpoint, headers=self._headers(), json=payload)
                if r.status_code == 200:
                    data = r.json()
                    break
                log.error("gateway %s -> %s: %s", self.endpoint,
                          r.status_code, r.text[:500])
                if (r.status_code >= 500 or "Out of Memory" in r.text
                        or "1210" in r.text):
                    if attempt == 0:
                        import asyncio
                        log.warning("gateway overloaded (%s), retry after 2s "
                                    "model=%s", r.status_code, self.model)
                        await asyncio.sleep(2)
                        continue
                    raise RuntimeError("推理服务当前繁忙（网关过载），请稍后重试")
                r.raise_for_status()
        # Anthropic 响应: content 是数组,含 {type: "thinking"|"text", text: "..."}
        content = data.get("content", [])
        for block in content:
            if block.get("type") == "text":
                return block.get("text", "")
        return ""

    async def abort(self) -> None:
        self._aborted = True
