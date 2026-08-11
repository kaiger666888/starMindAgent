"""QAStep 持久层：带乐观锁的落盘 + 探索树操作。

乐观锁：update 时 WHERE version = $expected，行数=0 抛 OptimisticLockConflict
（前端在途请求互斥的兜底——理论上前端已禁用，这里防并发写）。
"""
from __future__ import annotations
import logging
from dataclasses import dataclass
from typing import Optional

from sqlalchemy import select, update, func, text
from sqlalchemy.exc import NoResultFound

from app.db import session_scope
from app.models.tables import QAStep, QAStep as QAStepModel, ConceptNode, ConceptEdge, QASession
from app.config import settings
from app.qastep.state_machine import QAStatus, OptimisticLockConflict, IllegalTransition

log = logging.getLogger(__name__)


@dataclass
class CreatedQA:
    qa_id: str
    question: str


class QAStepRepository:
    # —— 创建 ——
    async def create(self, session_id: str, question: str,
                     parent_qa_id: str | None = None,
                     depth: int = 1, model: str = "unknown") -> CreatedQA:
        async with session_scope() as s:
            row = QAStep(
                session_id=session_id, parent_qa_id=parent_qa_id,
                question=question, status=QAStatus.GENERATING.value,
                depth=depth, model=model,
            )
            s.add(row)
            await s.flush()
            return CreatedQA(qa_id=row.qa_id, question=question)

    # —— 乐观锁状态迁移 ——
    async def transition(self, qa_id: str, nxt: QAStatus) -> None:
        async with session_scope() as s:
            # 读当前状态做合法迁移校验
            cur = (
                await s.execute(
                    select(QAStepModel.status, QAStepModel.version).where(QAStepModel.qa_id == qa_id)
                )
            ).first()
            if cur is None:
                raise NoResultFound(qa_id)
            # 合法性校验（与 state_machine 一致）
            from app.qastep.state_machine import QAStepRuntime
            QAStepRuntime.assert_transition(QAStatus(cur.status), nxt)

            res = await s.execute(
                update(QAStepModel)
                .where(QAStepModel.qa_id == qa_id, QAStepModel.version == cur.version)
                .values(status=nxt.value, version=cur.version + 1)
            )
            if res.rowcount == 0:
                raise OptimisticLockConflict(
                    f"version mismatch on qa_id={qa_id} (expected {cur.version})"
                )

    # —— 流式正文 checkpoint：增量落盘（harness 网络断开可从 offset 续推）——
    async def append_answer(self, qa_id: str, delta: str) -> None:
        async with session_scope() as s:
            # 用 PG 的 || 拼接，避免读改写竞态；answer_offset 跟随正文长度推进
            await s.execute(text(
                "UPDATE qa_step "
                "SET answer = COALESCE(answer,'') || :d, "
                "    answer_offset = char_length(COALESCE(answer,'') || :d), "
                "    version = version + 1 "
                "WHERE qa_id = :id"
            ).bindparams(d=delta, id=qa_id))

    async def restore_context(self, qa_id: str) -> dict:
        """回上层：恢复该 QAStep 的完整现场（answer + 概念 + offset）。"""
        async with session_scope() as s:
            row = (
                await s.execute(
                    select(QAStepModel).where(QAStepModel.qa_id == qa_id)
                )
            ).scalar_one_or_none()
            if row is None:
                raise NoResultFound(qa_id)
            return {
                "qa_id": row.qa_id, "session_id": row.session_id,
                "question": row.question, "answer": row.answer or "",
                "status": row.status, "answer_offset": row.answer_offset,
                "extracted_concept_ids": row.extracted_concept_ids,
            }

    # —— 膨胀控制：单会话 <=200 概念 ——
    async def is_bloat_limit_reached(self, session_id: str) -> bool:
        async with session_scope() as s:
            cnt = (
                await s.execute(
                    select(func.count(func.distinct(ConceptEdge.target_id)))
                    .where(ConceptEdge.session_id == session_id)
                )
            ).scalar_one()
            return cnt >= settings.max_concepts_per_session

    # —— fork 下钻子 QAStep ——
    async def fork_child(self, session_id: str, parent_qa_id: str,
                         concept_id: str, question: str | None) -> CreatedQA:
        async with session_scope() as s:
            parent = (
                await s.execute(
                    select(QAStepModel).where(QAStepModel.qa_id == parent_qa_id)
                )
            ).scalar_one()
            child_depth = parent.depth + 1
            if child_depth > settings.max_explore_depth:
                # 膨胀降级：超过 6 层，fork 一个仅展示已有概念、不调用推理的 QAStep
                # （此处简化：抛业务异常，由 API 返回 422 提示降级）
                raise DepthLimitReached(
                    f"explore depth limit reached (max {settings.max_explore_depth}); "
                    f"degrade to annotate-existing mode"
                )
            child = QAStep(
                session_id=session_id, parent_qa_id=parent_qa_id,
                question=question or f"[下钻 concept {concept_id}]",
                status=QAStatus.GENERATING.value, depth=child_depth,
            )
            s.add(child)
            # 记录 user_click 边（状态一）：source 取父 QAStep 首个已抽取概念，target 为被下钻概念
            parent_concepts = parent.extracted_concept_ids or []
            if parent_concepts:
                s.add(ConceptEdge(
                    session_id=session_id, source_id=parent_concepts[0],
                    target_id=concept_id, origin="user_click",
                ))
            await s.flush()
            return CreatedQA(qa_id=child.qa_id, question=child.question)

    # —— 概念同次抽取互相连边（状态二：co_occurrence）——
    async def link_co_occurrence(self, qa_id: str, session_id: str, concepts: list[dict]) -> None:
        if len(concepts) < 2:
            return
        async with session_scope() as s:
            ids = [c["concept_id"] for c in concepts if c.get("concept_id")]
            for i in range(len(ids)):
                for j in range(i + 1, len(ids)):
                    s.add(ConceptEdge(
                        session_id=session_id, source_id=ids[i], target_id=ids[j],
                        origin="co_occurrence",
                    ))
            await s.flush()

    # —— 埋点字段落盘 ——
    async def persist_telemetry(self, qa_id: str, *, model, prompt_hash,
                                raw_output, parsed_concepts, aliases, confidence) -> None:
        async with session_scope() as s:
            await s.execute(
                update(QAStepModel).where(QAStepModel.qa_id == qa_id).values(
                    model=model, prompt_hash=prompt_hash, raw_output=raw_output,
                    parsed_concepts=parsed_concepts, aliases=aliases, confidence=confidence,
                )
            )

    # —— 递归 CTE 查询整棵探索树（层级展开）——
    async def get_tree(self, session_id: str) -> list[dict]:
        async with session_scope() as s:
            rows = (
                await s.execute(text("""
                    WITH RECURSIVE tree AS (
                        SELECT qa_id, session_id, parent_qa_id, question, status, depth,
                               1 AS lvl, ARRAY[qa_id]::text[] AS path
                        FROM qa_step WHERE session_id = :sid AND parent_qa_id IS NULL
                        UNION ALL
                        SELECT c.qa_id, c.session_id, c.parent_qa_id, c.question, c.status, c.depth,
                               t.lvl + 1, t.path || c.qa_id::text
                        FROM qa_step c JOIN tree t ON c.parent_qa_id = t.qa_id
                        WHERE t.lvl < 20
                    )
                    SELECT * FROM tree ORDER BY path;
                """), {"sid": session_id})
            ).all()
            return [r._mapping for r in rows]


class DepthLimitReached(Exception):
    pass


repo = QAStepRepository()
