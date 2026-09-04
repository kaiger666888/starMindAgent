"""QAStep 持久层：带乐观锁的落盘 + 探索树操作。

乐观锁：update 时 WHERE version = $expected，行数=0 抛 OptimisticLockConflict
（前端在途请求互斥的兜底——理论上前端已禁用，这里防并发写）。
"""
from __future__ import annotations
import logging
from dataclasses import dataclass
from typing import Optional

from sqlalchemy import select, update, func, text
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.exc import NoResultFound

from app.db import session_scope
from app.models.tables import QAStep, QAStep as QAStepModel, ConceptNode, ConceptEdge, QASession
from app.config import settings
from app.qastep.state_machine import QAStatus, OptimisticLockConflict, IllegalTransition

log = logging.getLogger(__name__)


def _uuid_cast(param: str) -> str:
    """PG: CAST(:p AS uuid)；sqlite: :p（CAST 会按数字 affine 把 uuid 转 0）。"""
    from app.db import is_sqlite
    return param if is_sqlite() else f"CAST({param} AS uuid)"



def _is_uuid(v) -> bool:
    """concept_id 合法性：UUID 才能进 UUID 列（local_* 临时 id 会炸 asyncpg 编码）。"""
    if not v or not isinstance(v, str) or len(v) != 36:
        return False
    try:
        import uuid as _uuid
        _uuid.UUID(v)
        return True
    except (ValueError, AttributeError):
        return False


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
    async def reset_answer(self, qa_id: str) -> None:
        """清空正文（重跑幂等）：刷新/断线重连会重新订阅 stream 并重跑推理，
        append_answer 是纯追加（|| 拼接），不先清空会让正文越滚越长。"""
        async with session_scope() as s:
            await s.execute(text(
                "UPDATE qa_step SET answer = '', answer_offset = 0 "
                f"WHERE qa_id = {_uuid_cast(':id')}"
            ).bindparams(id=qa_id))

    async def append_answer(self, qa_id: str, delta: str) -> None:
        async with session_scope() as s:
            # 用 PG 的 || 拼接，避免读改写竞态；answer_offset 跟随正文长度推进
            await s.execute(text(
                "UPDATE qa_step "
                "SET answer = COALESCE(answer,'') || CAST(:d AS TEXT), "
                "    answer_offset = length(COALESCE(answer,'') || CAST(:d AS TEXT)), "
                "    version = version + 1 "
                f"WHERE qa_id = {_uuid_cast(':id')}"
            ).bindparams(d=delta, id=qa_id))

    async def update_layer_summary(self, qa_id: str, summary: str) -> None:
        """落盘层摘要（"这层你理解了什么"，作树节点折叠预览）。"""
        async with session_scope() as s:
            await s.execute(text(
                f"UPDATE qa_step SET layer_summary = :s WHERE qa_id = {_uuid_cast(':id')}"
            ).bindparams(s=summary, id=qa_id))

    async def reask(self, qa_id: str, question: str) -> None:
        """重问：in-place 改问题并清空旧回答现场，stream 重订阅即重跑推理。
        状态归位 generating：run() 在 sentinel 处走 generating->extracting 迁移，
        停留在 waiting 会被 assert_transition 判非法。
        概念/边不清理：旧概念节点与会话图共享，删边破坏已建立的概念图；
        重跑后新一轮抽取落 extracted_concept_ids 自然覆盖。"""
        async with session_scope() as s:
            res = await s.execute(text(
                "UPDATE qa_step SET question = :q, answer = '', answer_offset = 0, "
                "status = 'generating', layer_summary = NULL, version = version + 1 "
                f"WHERE qa_id = {_uuid_cast(':id')}"
            ).bindparams(q=question, id=qa_id))
            if res.rowcount == 0:
                raise NoResultFound(qa_id)

    async def mark_browsed_not_drilled(self, qa_id: str) -> None:
        """需求六"放弃探索"：用户回上层未下钻，标记 browsed_not_drilled。

        前提：该 QAStep 无子节点（无下钻）。若有子 QAStep，不算放弃。
        """
        async with session_scope() as s:
            # 子数 = 0 才算放弃
            child_cnt = (
                await s.execute(text(
                    f"SELECT count(*) FROM qa_step WHERE parent_qa_id = {_uuid_cast(':id')}"
                ).bindparams(id=qa_id))
            ).scalar() or 0
            if child_cnt == 0:
                await s.execute(text(
                    "UPDATE qa_step SET browsed_not_drilled = true "
                    f"WHERE qa_id = {_uuid_cast(':id')}"
                ).bindparams(id=qa_id))

    async def evaluate_concept_maturity(self, qa_id: str) -> int:
        """需求三"概念成熟度"：若概念 explore_count≥2 且该 QAStep 未下钻，
        标记 understood=true。返回被标记的概念数。
        """
        async with session_scope() as s:
            # 该 qa_step 抽取的 concept_ids
            row = (
                await s.execute(text(
                    "SELECT extracted_concept_ids FROM qa_step "
                    f"WHERE qa_id = {_uuid_cast(':id')}"
                ).bindparams(id=qa_id))
            ).first()
            if not row or not row[0]:
                return 0
            concept_ids = row[0]
            # 未下钻（无子）
            child_cnt = (
                await s.execute(text(
                    f"SELECT count(*) FROM qa_step WHERE parent_qa_id = {_uuid_cast(':id')}"
                ).bindparams(id=qa_id))
            ).scalar() or 0
            if child_cnt > 0:
                return 0  # 已下钻，不算"已理解"
            marked = 0
            for cid in concept_ids:
                cnt = (
                    await s.execute(text(
                        "SELECT explore_count FROM concept_node "
                        f"WHERE concept_id = {_uuid_cast(':id')}"
                    ).bindparams(id=str(cid)))
                ).scalar() or 0
                if cnt >= 2:
                    await s.execute(text(
                        "UPDATE concept_node SET understood = true "
                        f"WHERE concept_id = {_uuid_cast(':id')}"
                    ).bindparams(id=str(cid)))
                    marked += 1
            return marked

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
                # 子层继承父层 material_id：stream 路由按 material_id 现检索
                # 相关段落注入 prompt（不污染 question 存库）
                material_id=parent.material_id,
            )
            s.add(child)
            # 记录 user_click 边（状态一）：source 取父 QAStep 首个已抽取概念，target 为被下钻概念
            # target 非 UUID（前端"标为概念下钻"的 local_* 临时 id）时跳过建边，
            # 否则 asyncpg 编码炸 DataError 导致整个 fork 500
            parent_concepts = parent.extracted_concept_ids or []
            if parent_concepts and _is_uuid(concept_id):
                stmt = pg_insert(ConceptEdge).values(
                    session_id=session_id, source_id=parent_concepts[0],
                    target_id=concept_id, origin="user_click",
                ).on_conflict_do_nothing()
                await s.execute(stmt)
            await s.flush()
            return CreatedQA(qa_id=child.qa_id, question=child.question)

    # —— 概念同次抽取互相连边（状态二：co_occurrence）——
    async def link_co_occurrence(self, qa_id: str, session_id: str, concepts: list[dict]) -> None:
        if len(concepts) < 2:
            return
        async with session_scope() as s:
            ids = [c["concept_id"] for c in concepts if c.get("concept_id")]
            # 幂等：同一对概念已建边则跳过（ON CONFLICT DO NOTHING）
            for i in range(len(ids)):
                for j in range(i + 1, len(ids)):
                    stmt = pg_insert(ConceptEdge).values(
                        session_id=session_id, source_id=ids[i], target_id=ids[j],
                        origin="co_occurrence",
                    ).on_conflict_do_nothing()
                    await s.execute(stmt)
            await s.flush()

    # —— 埋点字段落盘 ——
    async def persist_telemetry(self, qa_id: str, *, model, prompt_hash,
                                raw_output, parsed_concepts, aliases, confidence,
                                extracted_concept_ids=None) -> None:
        async with session_scope() as s:
            vals = dict(model=model, prompt_hash=prompt_hash, raw_output=raw_output,
                        parsed_concepts=parsed_concepts, aliases=aliases, confidence=confidence)
            if extracted_concept_ids is not None:
                vals["extracted_concept_ids"] = extracted_concept_ids
            await s.execute(
                update(QAStepModel).where(QAStepModel.qa_id == qa_id).values(**vals)
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
