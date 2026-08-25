"""QA 路由：状态机三个出口 + SSE 流式回推。

技术架构文档：LLM 调用走异步任务队列 + SSE 流式回推。
路由组织：
  POST /qa/start              新问题（出口3：开新探索树）
  GET  /qa/{qa_id}/stream     SSE 流式推送 answer_delta / status / concepts / done
  POST /qa/{qa_id}/drilldown  点击概念下钻（出口1：fork 新 QAStep，挂 parent_qa_id）
  POST /qa/{qa_id}/rollback   回上层（出口2：栈式回退，状态保留）
"""
from __future__ import annotations
import json
import logging
from fastapi import APIRouter, HTTPException
from fastapi.responses import StreamingResponse

from app.qastep import repo, QAStatus, DepthLimitReached, IllegalTransition
from app.qastep.state_machine import QAStepPipeline
from app.qastep.repository import QAStepRepository, _uuid_cast
from app.concept import normalizer
from app.inference import StubInferenceSession
from app.schemas import QAStartRequest, QAStepOut, DriftDownRequest, RollbackRequest

log = logging.getLogger(__name__)
router = APIRouter(prefix="/qa", tags=["qa"])


def _pipeline_for(qa_id: str, session_id: str, question: str) -> QAStepPipeline:
    """构建 QAStepPipeline：推理会话走 harness 生产级 InferenceSession（真实推理不可用时 stub 兜底）。"""
    from app.harness.app import get_harness
    h = get_harness()
    inf = h.session_for(qa_id, session_id, question)
    pipe = QAStepPipeline(qa_id, session_id, question, inf, normalizer, repo)
    return pipe


@router.post("/start", response_model=QAStepOut)
async def start(req: QAStartRequest):
    """出口3：新问题 → 开新探索树（parent_qa_id=None）。

    学习材料：若 material_id 给定，从文件检索与 question 相关段落注入 prompt。
    """
    from app.db import session_scope, is_sqlite
    from sqlalchemy import update
    from app.models.tables import QASession, AppUser
    from datetime import datetime, timezone
    user_id = req.user_id or "default"
    # 学习材料上下文注入
    question = req.question
    if req.material_id:
        from app.learning import service as learning_service
        ctx = await learning_service.get_material_context(req.material_id, req.question)
        if ctx:
            question = f"【参考学习材料相关段落】\n{ctx}\n\n【用户问题】\n{req.question}"
    async with session_scope() as s:
        if is_sqlite():
            from sqlalchemy.dialects.sqlite import insert as sqlite_insert
            stmt = sqlite_insert(AppUser).values(
                user_id=user_id, last_active_at=datetime.now(timezone.utc)
            )
            stmt = stmt.on_conflict_do_update(
                index_elements=[AppUser.user_id],
                set_={"last_active_at": datetime.now(timezone.utc)},
            )
        else:
            from sqlalchemy.dialects.postgresql import insert as pg_insert
            stmt = pg_insert(AppUser).values(
                user_id=user_id, last_active_at=datetime.now(timezone.utc)
            )
            stmt = stmt.on_conflict_do_update(
                index_elements=[AppUser.user_id],
                set_={"last_active_at": datetime.now(timezone.utc)},
            )
        await s.execute(stmt)
        sess = QASession(user_id=user_id, domain_tag=req.domain_tag)
        s.add(sess)
        await s.flush()
        session_id = sess.session_id
    created = await repo.create(
        session_id=session_id, question=question, depth=1
    )
    # 挂 material_id（子层继承根层 material_id 用于上下文注入）
    if req.material_id:
        from sqlalchemy import text
        async with session_scope() as s:
            await s.execute(text(
                f"UPDATE qa_step SET material_id = {_uuid_cast(':mid')} WHERE qa_id = {_uuid_cast(':id')}"
            ).bindparams(mid=req.material_id, id=created.qa_id))
    return QAStepOut(
        qa_id=created.qa_id, session_id=session_id,
        parent_qa_id=None, question=created.question, answer=None,
        status=QAStatus.GENERATING.value, version=1, depth=1,
    )


@router.get("/{qa_id}/stream")
async def stream(qa_id: str):
    """SSE 流式回推：answer_delta / status / concepts / done。"""
    meta = await _load_meta(qa_id)
    pipe = _pipeline_for(qa_id, meta["session_id"], meta["question"])
    # 重建场景(刷新/断线重连):material context 不在 DB,按 material_id 现检索注入
    if meta.get("material_id"):
        from app.learning import service as learning_service
        ctx_detail = await learning_service.get_material_context_detail(
            str(meta["material_id"]), meta["question"]
        )
        if ctx_detail.get("context_text"):
            pipe.set_material_context(ctx_detail["context_text"])

    async def event_gen():
        try:
            async for ev in pipe.run():
                yield f"data: {json.dumps(ev, ensure_ascii=False)}\n\n"
        except IllegalTransition as e:
            yield f"data: {json.dumps({'type':'error','message':str(e)})}\n\n"
        except DepthLimitReached as e:
            yield f"data: {json.dumps({'type':'degraded','message':str(e)})}\n\n"

    return StreamingResponse(event_gen(), media_type="text/event-stream")


@router.post("/{qa_id}/drilldown", response_model=QAStepOut)
async def drilldown(qa_id: str, req: DriftDownRequest):
    """出口1：点击概念下钻 → fork 新 QAStep，挂 parent_qa_id。

    需求五"差异化引导"：若该概念 explore_count≥2，查历史语境注入 prompt——
    "你之前在 X 语境下了解过这个概念，这里补充它在当前语境下的不同侧面"。
    """
    from app.concept import concept_service
    parent = await _load_meta(qa_id)
    # 查概念历史语境（前端"标为概念下钻"用 local_* 临时 id，非 UUID，
    # 直接进 UUID 列查询会让 asyncpg 在编码阶段抛 DataError -> 500。
    # 非 UUID 视为无历史新概念，跳过查询）
    question = req.question
    if _is_uuid(req.concept_id):
        history = await concept_service.get_concept_history(req.concept_id)
    else:
        history = None
    if history and history.get("explore_count", 0) >= 2:
        contexts = history.get("contexts", [])
        prior_qs = [c["question"] for c in contexts[:3] if c.get("question")]
        if prior_qs:
            hint = (
                f"（你之前在「{'、'.join(prior_qs)}」的语境下了解过【{history.get('canonical_name', '')}】，"
                f"这里请补充它在当前语境下的不同侧面，避免与前述内容重复。）"
            )
            question = f"{hint} {question}"
    # 学习材料上下文：查 parent QAStep 的 material_id（子层继承根层 material_id）
    # 只把检索到的段落塞进 QAStepOut.context 供前端展示 + 注入推理 prompt,
    # 不再拼进 question 存库(否则子层标题被污染成几百字拼接串)
    from app.db import session_scope
    from sqlalchemy import select, text as sql_text
    from app.models.tables import QAStep as _QS
    async with session_scope() as s:
        mid = (await s.execute(select(_QS.material_id).where(_QS.qa_id == qa_id))).scalar_one_or_none()
    drill_context = None
    if mid:
        from app.learning import service as learning_service
        ctx_detail = await learning_service.get_material_context_detail(str(mid), question)
        if ctx_detail.get("context_text"):
            drill_context = {
                "snippets": ctx_detail.get("snippets", []),
                "preparation": ctx_detail.get("preparation", 0.0),
                "matched_chunks": ctx_detail.get("matched_chunks", 0),
                "total_chunks": ctx_detail.get("total_chunks", 0),
                "context_text": ctx_detail["context_text"],  # 注入推理 prompt 用
            }
    # _pipeline_for 读 DB 的 question,而 DB 里子层还没建。传父 question 作底,
    # 真正的子层 question 在 fork_child 里用 question 变量(干净版)落盘。
    pipe = _pipeline_for(qa_id, parent["session_id"], question)
    try:
        new_pipe = await pipe.drill_down(
            parent_qa_id=qa_id, concept_id=req.concept_id, question=question
        )
    except DepthLimitReached as e:
        # 膨胀降级：超 6 层 -> 标注已有概念，不新建推理
        raise HTTPException(status_code=422, detail=str(e))
    # material context 不在 drilldown 注入（pipeline 在 stream 路由会被重建，
    # 这里的 set 对 stream 无效）。子层 material_id 在 fork_child 已继承，
    # stream 路由按 material_id 现检索注入。这里只在 QAStepOut.context 回前端展示。
    # 返回给前端的 question 是干净版(req.question 或概念名),不含材料 context
    return QAStepOut(
        qa_id=new_pipe.qa_id, session_id=new_pipe.session_id,
        parent_qa_id=qa_id, question=question, answer=None,
        status=QAStatus.GENERATING.value, version=1, depth=0,
        context=drill_context,
    )


@router.post("/{qa_id}/rollback")
async def rollback(qa_id: str, req: RollbackRequest):
    """出口2：回上层 → 栈式回退，状态保留（恢复 target 现场）。

    需求六"放弃探索"：回上层意味着当前 QAStep 未下钻，标记 browsed_not_drilled。
    需求三"概念成熟度"：若该 QAStep 涉及概念 explore_count≥2 且未下钻，
      标记 understood（前端入口变灰）。
    """
    # 标记当前 qa 为已浏览未下钻
    await repo.mark_browsed_not_drilled(qa_id)
    # 涉及概念成熟度判定
    await repo.evaluate_concept_maturity(qa_id)
    ctx = await repo.restore_context(req.target_qa_id)
    return ctx


# —— 工具 ——
def _is_uuid(v) -> bool:
    """concept_id 合法性：UUID 才能进 UUID 列查询（前端"标为概念下钻"的
    local_* 临时 id 会让 asyncpg 在编码阶段抛 DataError -> 500）。"""
    if not v or not isinstance(v, str) or len(v) != 36:
        return False
    try:
        import uuid as _uuid
        _uuid.UUID(v)
        return True
    except (ValueError, AttributeError):
        return False


async def _load_meta(qa_id: str) -> dict:
    from sqlalchemy import select
    from app.db import session_scope
    from app.models.tables import QAStep
    async with session_scope() as s:
        row = (
            await s.execute(select(QAStep).where(QAStep.qa_id == qa_id))
        ).scalar_one_or_none()
        if row is None:
            raise HTTPException(status_code=404, detail=f"qa_step {qa_id} not found")
        return {"session_id": row.session_id, "question": row.question,
                "status": row.status, "material_id": row.material_id}
