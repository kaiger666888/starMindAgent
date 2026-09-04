"""QAStep 状态机（后端核心，P0）。

技术架构文档第三节：每个下钻层是一个 QAStep 实例，显式状态机驱动而非递归调用。
用户动作只有三个出口：
    1) 点击概念下钻 → fork 新 QAStep，挂 parent_qa_id
    2) 回上层 → 栈式回退，状态保留（不删除子树）
    3) 新问题 → 开新探索树（parent_qa_id = NULL）
LLM 只在单步内执行「回答 + 结构化抽取」固定 pipeline。

状态流转：generating → extracting → waiting
并发一致性：乐观锁 version + 前端在途请求互斥（同一 QAStep 未完成时禁用其他操作）
"""
from __future__ import annotations
import hashlib
import logging
from dataclasses import dataclass
from enum import Enum
from typing import AsyncIterator, Optional

from app.config import settings
from app.schemas import ConceptBlock, ConceptItem

log = logging.getLogger(__name__)


class QAStatus(str, Enum):
    GENERATING = "generating"
    EXTRACTING = "extracting"
    WAITING = "waiting"


class QATransition(Enum):
    """状态机合法迁移，非法迁移抛 IllegalTransition。值用 (from, to) 元组表示。"""
    DONE_STREAMING = ("generating", "extracting")
    DONE_EXTRACTING = ("extracting", "waiting")
    # 推理 error 即终态：立即回 waiting（客户端收到 error 事件即断开 SSE，
    # generator 被关闭，若不先落状态 qa 会永远卡在 generating/extracting）
    ERROR_ABORT = ("generating", "waiting")
    ERROR_ABORT_EXTRACTING = ("extracting", "waiting")
    # 三个出口都从 waiting 出发
    DRILL_DOWN = ("waiting", "generating")     # fork 新 QAStep
    ROLLBACK = ("waiting", "waiting")           # 栈式回退，状态保留
    NEW_TREE = ("waiting", "generating")        # 新问题，开新树


@dataclass
class QAStepRuntime:
    """一次 QAStep 运行期的业务语义视图（与持久层解耦）。

    Harness 工程师提供 InferenceSession 封装，QAStep 只关心业务语义：
    - 消费 InferenceSession 的流式 token 事件累 answer
    - 在 extracting 阶段消费尾部 ConceptBlock，触发归一化
    - waiting 阶段等待用户三个出口动作
    """
    qa_id: str
    session_id: str
    question: str
    model: str = "unknown"

    # —— 状态机入口 ——
    @staticmethod
    def assert_transition(cur: QAStatus, nxt: QAStatus) -> None:
        legal = {(t.value[0], t.value[1]) for t in QATransition}
        if (cur.value, nxt.value) not in legal:
            raise IllegalTransition(f"illegal QAStep transition {cur} -> {nxt}")

    @staticmethod
    def prompt_hash(question: str, parent_chain: list[str] | None = None) -> str:
        """埋点 prompt_hash：对问题 + 父链摘要哈希。"""
        blob = question + "|" + ":".join(parent_chain or [])
        return hashlib.sha256(blob.encode("utf-8")).hexdigest()[:16]


class IllegalTransition(Exception):
    pass


class OptimisticLockConflict(Exception):
    """乐观锁版本号不匹配：前端在途请求互斥失败的兜底。"""


# ---------------------------------------------------------------------------
# QAStep pipeline：generating -> extracting -> waiting
# 由 inference 层驱动：流式产出正文 -> sentinel -> 解析概念 -> 落盘 waiting
# ---------------------------------------------------------------------------
class QAStepPipeline:
    """驱动单个 QAStep 走完 generating -> extracting -> waiting。

    与持久层 (QAStepRepository) 解耦：pipeline 吐出语义事件，
    repository 负责带乐观锁的落盘。
    """

    def __init__(self, qa_id: str, session_id: str, question: str,
                 inference_session, normalizer, repository):
        """
        inference_session: 实现 InferenceSession Protocol 的对象（Harness 工程师提供）
        normalizer: concept.normalization.ConceptNormalizer
        repository: qastep.repository.QAStepRepository
        """
        self.qa_id = qa_id
        self.session_id = session_id
        self.question = question
        self.inference = inference_session
        self.normalizer = normalizer
        self.repo = repository
        self.rt = QAStepRuntime(qa_id, session_id, question)

    def set_material_context(self, ctx: str | None) -> None:
        """转发学习材料相关段落,推理 prompt 用,不污染 question 存库。

        转发给 inference session -> InferenceClient,在 stream() 拼 prompt 时用。
        """
        if ctx and hasattr(self.inference, "set_material_context"):
            self.inference.set_material_context(ctx)

    def set_selection(self, sel: str | None) -> None:
        """就选段提问：转发用户选中的原文，推理 prompt 用，不污染 question 存库。"""
        if sel and hasattr(self.inference, "set_selection"):
            self.inference.set_selection(sel)

    def set_chain(self, chain) -> None:
        """注入概念链（下钻路径），推理 prompt 填概念链槽（防回答同质化）。

        转发给 inference session -> InferenceClient，stream() 组 prompt 时用。
        """
        if chain and hasattr(self.inference, "set_chain"):
            self.inference.set_chain(chain)

    async def run(self) -> AsyncIterator[dict]:
        """流式产出事件，供 SSE 推给前端。

        事件类型：
          {type: 'answer_delta', text}    — 正文增量（逐 token 渲染）
          {type: 'status', status}        — 状态机变更
          {type: 'concepts', concepts[]}  — 解析完成的概念（已归一化）
          {type: 'done', qa_id}           — 进入 waiting
        """
        # —— generating：流式正文 ——
        # QAStep 在 repo.create() 时已置 GENERATING，这里只发语义通知，不重复转换
        # 重跑幂等：刷新/断线重连会重新订阅 stream 并重跑推理，append_answer 是
        # 纯追加（|| 拼接），不清空旧 answer 会让正文越滚越长（实测一层累积到
        # 7900+ 字），L1 兜底把整段正文塞回 prompt，网关内存被越推越高（OOM 放大器）。
        await self.repo.reset_answer(self.qa_id)
        yield {"type": "status", "status": QAStatus.GENERATING.value}

        answer_buf: list[str] = []
        concept_block: ConceptBlock | None = None
        # 流式增量候选：正文每累积一段（句界）跑一次本地 jieba 候选抽取，
        # 新词经 concept_candidates 事件推前端（阅读时动态高亮，不等整层
        # 生成完的权威 concepts）。纯本地 CPU，ms 级，无新增 LLM 调用。
        from app.qastep.stream_candidates import StreamCandidateExtractor
        cand = StreamCandidateExtractor(self.question)

        async for ev in self.inference.stream():
            # 推理框架按协议产出三类原始事件：
            #   {kind:'delta', text}          正文增量
            #   {kind:'sentinel'}             sentinel 已切分，进入 JSON 累积
            #   {kind:'json_done', block}      尾部 JSON 解析完成
            kind = ev["kind"]
            if kind == "delta":
                answer_buf.append(ev["text"])
                await self.repo.append_answer(self.qa_id, ev["text"])  # checkpoint 落盘
                yield {"type": "answer_delta", "text": ev["text"]}
                # 增量候选（推过就不再重推，前端只并入不覆盖）
                fresh = cand.feed(ev["text"])
                if fresh:
                    yield {"type": "concept_candidates", "concepts": fresh}
            elif kind == "sentinel":
                # 正文已全部流出，进入 extracting（幂等：已是 EXTRACTING 则跳过）
                from app.db import session_scope
                from sqlalchemy import select as _sel
                from app.models.tables import QAStep as _QS
                async with session_scope() as _s:
                    cur = (await _s.execute(_sel(_QS.status).where(_QS.qa_id == self.qa_id))).scalar_one_or_none()
                if cur != QAStatus.EXTRACTING.value:
                    await self.repo.transition(self.qa_id, QAStatus.EXTRACTING)
                yield {"type": "status", "status": QAStatus.EXTRACTING.value}
            elif kind == "json_done":
                concept_block = ev["block"]
            elif kind == "error":
                # 正文已开始流式渲染 -> 不可重试，降级（L1）
                yield {"type": "error", "message": ev.get("message", "inference error")}
                # error 即终态：客户端收到即断开 SSE，generator 会被关闭，
                # 必须在此落 waiting，否则 qa 永远卡 generating/extracting
                try:
                    await self.repo.transition(self.qa_id, QAStatus.WAITING)
                except Exception as e:  # noqa: BLE001  状态可能已被并发迁移
                    log.warning("error-abort transition failed qa_id=%s: %s", self.qa_id, e)
                break

        # -- 联网搜索来源（backend agentic 轮触发过搜索时非空）--
        # 读 backend.last_search_sources 推给前端渲染"参考来源"块
        try:
            # InferenceSession 的 client 是私有命名 _client（.backend/.client 都拿不到）
            _backend = getattr(self.inference, 'backend', None)
            if _backend is None:
                _client = (getattr(self.inference, 'client', None)
                           or getattr(self.inference, '_client', None))
                _backend = getattr(_client, 'backend', None) if _client else None
            _sources = getattr(_backend, 'last_search_sources', None)
            if _sources:
                yield {'type': 'search_sources', 'sources': _sources[:6]}
        except Exception:  # noqa: BLE001  来源展示失败不影响主链路
            pass

        # —— extracting：概念归一化 + 落盘 ——
        concepts_out: list[dict] = []
        has_concepts = concept_block is not None and concept_block.concepts
        if has_concepts:
            # 膨胀控制：超限降级为只标注已有概念（不新增）
            degraded = await self.repo.is_bloat_limit_reached(self.session_id)
            concepts_out = await self._normalize_concepts_parallel(
                concept_block.concepts, degraded)
            # 建边（co_occurrence：同次抽取的概念互相连边）
            await self.repo.link_co_occurrence(self.qa_id, self.session_id, concepts_out)
            yield {"type": "concepts", "concepts": concepts_out}
        else:
            # L1 兜底：LLM 未按 sentinel 协议输出 ConceptBlock，
            # 用 complete_text 二次抽取概念（L2 拆分模式）
            fallback = await self._extract_concepts_fallback("".join(answer_buf))
            if fallback:
                degraded = await self.repo.is_bloat_limit_reached(self.session_id)
                concepts_out = await self._normalize_concepts_parallel(
                    fallback, degraded)
                await self.repo.link_co_occurrence(self.qa_id, self.session_id, concepts_out)
                yield {"type": "concepts", "concepts": concepts_out}

        # -- 层摘要：后台异步生成（不阻塞 done）--
        # 实测层摘要是又一次完整 LLM 往返（TTFT ~10-25s），串行等待让用户
        # 从提问到 done 多等近一倍时间，而树节点折叠预览并非当下必需。
        # 后台完成后落库；用户切层/恢复会话时读 DB 可见。
        self._spawn_layer_summary_bg("".join(answer_buf), concepts_out)

        # 埋点落盘后台化（评测依赖，不阻塞 done）：
        # persist_telemetry 是一次 DB update，emit_telemetry 是 NDJSON 文件写，
        # 串行合计 ~0.5s；done 不等它们，后台完成后评测管线照常回放。
        self._spawn_telemetry_bg("".join(answer_buf), concepts_out, concept_block)
        # —— waiting ——
        await self.repo.transition(self.qa_id, QAStatus.WAITING)
        yield {"type": "status", "status": QAStatus.WAITING.value}
        yield {"type": "done", "qa_id": self.qa_id}

    async def _normalize_concepts_parallel(self, items: list, degraded: bool) -> list[dict]:
        """概念归一化并行化（asyncio.gather）。

        实测 5 概念串行 normalize+建边 1.8s（每个 2-3 次 DB 往返），
        gather 并发后 ~0.5s。同批概念名互不冲突，asyncpg 连接池并发安全。
        degraded 模式只匹配不新建（膨胀控制语义不变）。
        单项失败跳过（None 过滤），不让一个坏概念毁整层。
        """
        import asyncio

        async def one(item):
            name = item.name if hasattr(item, "name") else item.get("name")
            if degraded:
                return await self.normalizer.match_existing_only(name, self.session_id)
            ci = item if hasattr(item, "name") else None
            if ci is None:
                from app.schemas import ConceptItem
                ci = ConceptItem(name=name, aliases=item.get("aliases", []),
                                 confidence=float(item.get("confidence", 0.7)))
            return await self.normalizer.normalize(ci, self.qa_id, self.session_id)

        results = await asyncio.gather(*(one(it) for it in items),
                                       return_exceptions=True)
        return [r for r in results if isinstance(r, dict)]

    def _spawn_telemetry_bg(self, answer_text, concepts_out, concept_block) -> None:
        """埋点落盘后台任务：DB telemetry + NDJSON 评测埋点，异常只记日志。"""
        import asyncio

        model = concept_block.model if concept_block else self.rt.model
        prompt_hash = QAStepRuntime.prompt_hash(self.question)

        async def _bg() -> None:
            try:
                await self.repo.persist_telemetry(
                    self.qa_id,
                    model=model,
                    prompt_hash=prompt_hash,
                    raw_output=answer_text,
                    parsed_concepts=[c["canonical_name"] for c in concepts_out],
                    aliases=[a for c in concepts_out for a in c.get("aliases", [])],
                    confidence=_avg_confidence(concept_block),
                    extracted_concept_ids=[c["concept_id"] for c in concepts_out if c.get("concept_id")],
                )
            except Exception as e:  # noqa: BLE001
                log.warning("telemetry (bg) failed qa_id=%s: %r", self.qa_id, e)
            try:
                from app.qastep.telemetry import emit_telemetry
                emit_telemetry(
                    qa_id=self.qa_id, session_id=self.session_id,
                    model=model, prompt_hash=prompt_hash,
                    raw_output=answer_text, answer_text=answer_text,
                    parsed_concepts=[{
                        "canonical_name": c.get("canonical_name"),
                        "aliases": c.get("aliases", []),
                        "confidence": c.get("confidence", 0.0),
                        "raw_text": c.get("canonical_name"),
                    } for c in concepts_out],
                    confidence=_avg_confidence(concept_block),
                    depth=getattr(self, "_depth", 1),
                    parent_qa_id=getattr(self, "_parent_qa_id", None),
                    parent_concept_chain=getattr(self, "_chain", None),
                )
            except Exception as e:  # noqa: BLE001
                log.warning("emit_telemetry (bg) failed qa_id=%s: %r", self.qa_id, e)

        try:
            self._telemetry_task = asyncio.create_task(_bg())
        except RuntimeError:
            log.info("no loop, skip bg telemetry qa_id=%s", self.qa_id)

    def _spawn_layer_summary_bg(self, answer_text: str, concepts: list[dict]) -> None:
        """后台生成层摘要：create_task 派发，异常只记日志（不打断主流程）。

        done 事件不等它；完成后落库（树节点预览在下次进入该层/恢复会话时可见）。
        引用保存在实例上防 GC；同 qa 重跑时旧任务自然作废（reset_answer 后
        生成的新摘要覆盖旧值）。
        """
        import asyncio

        async def _bg() -> None:
            try:
                summary = await self._gen_layer_summary(answer_text, concepts)
                if summary:
                    await self.repo.update_layer_summary(self.qa_id, summary)
                    log.info("layer summary (bg) saved qa_id=%s len=%d",
                             self.qa_id, len(summary))
            except Exception as e:  # noqa: BLE001  后台任务，失败不影响主链路
                log.warning("layer summary (bg) failed qa_id=%s: %r", self.qa_id, e)

        try:
            self._layer_summary_task = asyncio.create_task(_bg())
        except RuntimeError:  # 无事件循环（测试/同步上下文）：退化为同步跳过
            log.info("no loop, skip bg layer summary qa_id=%s", self.qa_id)

    async def _gen_layer_summary(self, answer_text: str, concepts: list[dict]) -> str | None:
        """生成"这层你理解了什么"层摘要（≤60字），作树节点折叠预览。"""
        if not answer_text.strip():
            return None
        # 取后端：self.inference 可能是 harness.InferenceSession（含 .client.backend）
        # 或 InferenceClient（含 .backend）；都拿不到就用 default_backend()
        from app.inference.backend import default_backend
        backend = getattr(self.inference, "backend", None)
        if backend is None:
            client = getattr(self.inference, "client", None)
            backend = getattr(client, "backend", None) if client else None
        if backend is None or not hasattr(backend, "complete_text"):
            backend = default_backend()
        if not hasattr(backend, "complete_text"):
            return None  # StubLLMBackend 无 complete_text，跳过
        names = "、".join(c.get("canonical_name", "") for c in concepts[:8]) if concepts else "（未抽取到概念）"
        system = (
            "你用一句话（不超过60字）概括用户这层问答理解了什么，"
            "基于答案和抽取的概念。直接输出概括句，不加前缀和引号。"
        )
        user = f"问题：{self.question}\n答案摘要：{answer_text[:600]}\n抽取概念：{names}"
        try:
            # 网关 thinking 模式下首个可见 token 前有长思考期，20s 会 ReadTimeout
            return await backend.complete_text(system, user, timeout=45.0)
        except Exception as e:
            log.warning("layer summary failed qa_id=%s: %r", self.qa_id, e)
            return None

    async def _extract_concepts_fallback(self, answer_text: str) -> list[dict] | None:
        """L1 兜底：LLM 未按 sentinel 协议输出时，二次调 LLM 抽取概念。

        返回 [{name, aliases[], confidence}] 或 None。
        """
        if not answer_text.strip():
            return None
        from app.inference.backend import default_backend
        backend = getattr(self.inference, "backend", None)
        if backend is None:
            client = getattr(self.inference, "client", None)
            backend = getattr(client, "backend", None) if client else None
        if backend is None:
            backend = default_backend()
        if not hasattr(backend, "complete_text"):
            return None
        system = (
            "从下面这段回答中抽取 3-8 个关键概念。用 JSON 数组返回，"
            "每项 {name, aliases:[], confidence:0.0-1.0}。"
            "name 用概念规范名(2-6字)，aliases 含中英文/缩写别名。只输出 JSON。"
        )
        user = f"问题：{self.question}\n回答：{answer_text[:1500]}"
        try:
            import json as _json, re
            raw = await backend.complete_text(system, user, timeout=60.0)
            # 去掉 markdown 代码块包裹
            cleaned = re.sub(r"^```(?:json)?\s*", "", raw.strip(), flags=re.IGNORECASE)
            cleaned = re.sub(r"\s*```\s*$", "", cleaned).strip()
            m = re.search(r"\[[\s\S]*\]", cleaned)
            if not m:
                log.warning("extract fallback: no JSON array found in LLM response")
                return None
            items = _json.loads(m.group(0))
            from app.schemas import ConceptItem
            return [
                ConceptItem(name=it.get("name", ""), aliases=it.get("aliases", []),
                            confidence=float(it.get("confidence", 0.7)))
                for it in items if it.get("name")
            ]
        except Exception as e:
            log.warning("extract fallback failed qa_id=%s: %r", self.qa_id, e)
            return None

    async def drill_down(self, parent_qa_id: str, concept_id: str,
                         question: str | None = None) -> "QAStepPipeline":
        """出口1：点击概念下钻 → fork 新 QAStep，挂 parent_qa_id。"""
        # 新 QAStep depth = parent.depth + 1，触发器 / repository 兜底膨胀上限
        new_qa = await self.repo.fork_child(
            self.session_id, parent_qa_id, concept_id, question
        )
        return QAStepPipeline(
            new_qa.qa_id, self.session_id, new_qa.question,
            self.inference, self.normalizer, self.repo,
        )

    async def rollback(self, target_qa_id: str) -> dict:
        """出口2：回上层 → 栈式回退，状态保留（不删子树，恢复 target 现场）。"""
        # 用户回到该层应看到完整现场而非空白重来（Harness 层负责 checkpoint）
        return await self.repo.restore_context(target_qa_id)


def _avg_confidence(block: ConceptBlock | None) -> float:
    if not block or not block.concepts:
        return 0.0
    return sum(c.confidence for c in block.concepts) / len(block.concepts)
