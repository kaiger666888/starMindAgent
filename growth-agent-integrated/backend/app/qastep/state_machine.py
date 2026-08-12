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
        yield {"type": "status", "status": QAStatus.GENERATING.value}

        answer_buf: list[str] = []
        concept_block: ConceptBlock | None = None

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
            elif kind == "sentinel":
                # 正文已全部流出，进入 extracting
                await self.repo.transition(self.qa_id, QAStatus.EXTRACTING)
                yield {"type": "status", "status": QAStatus.EXTRACTING.value}
            elif kind == "json_done":
                concept_block = ev["block"]
            elif kind == "error":
                # 正文已开始流式渲染 -> 不可重试，降级（L1）
                yield {"type": "error", "message": ev.get("message", "inference error")}
                break

        # —— extracting：概念归一化 + 落盘 ——
        concepts_out: list[dict] = []
        has_concepts = concept_block is not None and concept_block.concepts
        if has_concepts:
            # 膨胀控制：超限降级为只标注已有概念（不新增）
            degraded = await self.repo.is_bloat_limit_reached(self.session_id)
            for item in concept_block.concepts:
                if degraded:
                    # 降级：只尝试匹配已有概念，不新建
                    matched = await self.normalizer.match_existing_only(item.name, self.session_id)
                    if matched:
                        concepts_out.append(matched)
                else:
                    resolved = await self.normalizer.normalize(
                        item, self.qa_id, self.session_id
                    )
                    concepts_out.append(resolved)
            # 建边（co_occurrence：同次抽取的概念互相连边）
            await self.repo.link_co_occurrence(self.qa_id, self.session_id, concepts_out)
            yield {"type": "concepts", "concepts": concepts_out}
        else:
            # L1 兜底：LLM 未按 sentinel 协议输出 ConceptBlock，
            # 用 complete_text 二次抽取概念（L2 拆分模式）
            fallback = await self._extract_concepts_fallback("".join(answer_buf))
            if fallback:
                degraded = await self.repo.is_bloat_limit_reached(self.session_id)
                for item in fallback:
                    if degraded:
                        matched = await self.normalizer.match_existing_only(item["name"], self.session_id)
                        if matched:
                            concepts_out.append(matched)
                    else:
                        resolved = await self.normalizer.normalize(
                            item, self.qa_id, self.session_id
                        )
                        concepts_out.append(resolved)
                await self.repo.link_co_occurrence(self.qa_id, self.session_id, concepts_out)
                yield {"type": "concepts", "concepts": concepts_out}

        # —— 层摘要：在概念列表之上生成"这层你理解了什么"，作树节点折叠预览 ——
        layer_summary = await self._gen_layer_summary("".join(answer_buf), concepts_out)
        if layer_summary:
            await self.repo.update_layer_summary(self.qa_id, layer_summary)
            yield {"type": "layer_summary", "layer_summary": layer_summary}

        # 埋点字段落盘（评测依赖）
        await self.repo.persist_telemetry(
            self.qa_id,
            model=self.rt.model if False else concept_block.model if concept_block else self.rt.model,
            prompt_hash=QAStepRuntime.prompt_hash(self.question),
            raw_output="".join(answer_buf),
            parsed_concepts=[c["canonical_name"] for c in concepts_out],
            aliases=[a for c in concepts_out for a in c.get("aliases", [])],
            confidence=_avg_confidence(concept_block),
            extracted_concept_ids=[c["concept_id"] for c in concepts_out if c.get("concept_id")],
        )
        # —— 评测埋点 NDJSON（11 字段，评测管线按 qa_id 回放比对 golden set）——
        from app.qastep.telemetry import emit_telemetry
        emit_telemetry(
            qa_id=self.qa_id, session_id=self.session_id,
            model=concept_block.model if concept_block else self.rt.model,
            prompt_hash=QAStepRuntime.prompt_hash(self.question),
            raw_output="".join(answer_buf),
            answer_text="".join(answer_buf),
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
        # —— waiting ——
        await self.repo.transition(self.qa_id, QAStatus.WAITING)
        yield {"type": "status", "status": QAStatus.WAITING.value}
        yield {"type": "done", "qa_id": self.qa_id}

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
            return await backend.complete_text(system, user, timeout=20.0)
        except Exception as e:
            log.warning("layer summary failed: %s", e)
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
            raw = await backend.complete_text(system, user, timeout=25.0)
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
            log.warning("extract fallback failed: %s", e)
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
