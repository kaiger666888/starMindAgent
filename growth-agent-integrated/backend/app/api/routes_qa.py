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
from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import StreamingResponse

from app.qastep import repo, QAStatus, DepthLimitReached, IllegalTransition
from app.qastep.state_machine import QAStepPipeline
from app.qastep.repository import QAStepRepository, _uuid_cast
from app.concept import normalizer
from app.inference import StubInferenceSession
from app.schemas import QAStartRequest, QAStepOut, DriftDownRequest, RollbackRequest, ReAskRequest

log = logging.getLogger(__name__)
router = APIRouter(prefix="/qa", tags=["qa"])


async def _pipeline_for(qa_id: str, session_id: str, question: str) -> QAStepPipeline:
    """构建 QAStepPipeline：推理会话走 harness 生产级 InferenceSession（真实推理不可用时 stub 兜底）。"""
    from app.harness.app import get_harness
    h = get_harness()
    inf = h.session_for(qa_id, session_id, question)
    pipe = QAStepPipeline(qa_id, session_id, question, inf, normalizer, repo)
    # 概念链注入：沿 parent_qa_id 回溯构建下钻路径（浅->深），填 build_prompt
    # 概念链槽。此前该槽从未被填充 -> L2+ 回答无路径上下文，模型只能泛讲
    # 主题（"对最高层内容的概括性解读"的同质化根因）。
    # 链节点带每层 layer_summary（后台总结缓存），滑窗传导进 prompt。
    chain = await _build_chain(qa_id)
    if chain:
        pipe.set_chain(chain)
    return pipe


async def _build_chain(qa_id: str, max_len: int = 10) -> list:
    """沿 parent_qa_id 向上回溯，构建从根到父层的概念链（ChainNode 列表，浅->深）。

    ChainNode.concept 从父层 question 提取（下钻问句格式「深入解释「X」」取 X；
    旧数据/自由问句取前 20 字）；ChainNode.summary 带上该层后台生成的总结
    （layer_summary），让深层的 prompt 里有祖先层的语义脉络（滑动窗口传导）。
    返回列表不含当前层自身。
    """
    import re as _re
    from app.inference.context import ChainNode
    from app.db import session_scope
    from sqlalchemy import select as _sel
    from app.models.tables import QAStep as _QS
    nodes: list = []
    cur = qa_id
    seen: set = set()
    for _ in range(max_len):
        if not cur or cur in seen:
            break
        seen.add(cur)
        async with session_scope() as s:
            row = (
                await s.execute(_sel(_QS.parent_qa_id, _QS.question, _QS.depth,
                                    _QS.layer_summary)
                .where(_QS.qa_id == cur))
            ).first()
        if row is None:
            break
        parent_qa_id, question, depth, layer_summary = row
        if parent_qa_id is None:
            break  # 当前层是根，链到此为止（根层自身不入链）
        # 提取父层的"被下钻概念"：下钻问句取「X」，否则剥疑问前缀后取前 20 字
        m = _re.search(r"「(.+?)」", question or "")
        if m:
            concept = m.group(1)
        else:
            concept = _re.sub(
                r"^(什么是|请(详细)?(解释|介绍|说明)|如何理解|为什么|详述|解释|介绍)\s*",
                "", (question or "").strip())[:20]
        nodes.append(ChainNode(depth=depth, question=question or "",
                               concept=concept, siblings=[],
                               summary=(layer_summary or "").strip()))
        cur = parent_qa_id
    nodes.reverse()  # 浅 -> 深
    return nodes


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
        # qa_step 建行并入同一事务（原 repo.create 独立 session_scope =
        # 多一次 commit 往返 ~0.5s；实测 start 总耗时 1.05s 里 DB 往返占大头）
        from app.models.tables import QAStep as _QAStep
        qa = _QAStep(session_id=session_id, question=question,
                     status=QAStatus.GENERATING.value, depth=1,
                     model="unknown",
                     material_id=req.material_id)
        s.add(qa)
        await s.flush()
        qa_id = qa.qa_id
    created_qa_id, created_question = qa_id, question
    return QAStepOut(
        qa_id=created_qa_id, session_id=session_id,
        parent_qa_id=None, question=created_question, answer=None,
        status=QAStatus.GENERATING.value, version=1, depth=1,
    )


# 就选段提问：drilldown 时暂存选中原文（进程内），stream 订阅时注入 prompt。
# 不落库：selection 是一次性语境，重跑（刷新/reask）后失效是合理语义。
# 同一子层的 stream 订阅有 inflight 互斥 + SessionManager 按 qa_id 缓存，
# 暂存字典只需小容量，超量丢最旧。
_SELECTION_CACHE: dict[str, str] = {}
_SELECTION_CACHE_MAX = 64


def _remember_selection(qa_id: str, selection: str) -> None:
    if len(_SELECTION_CACHE) >= _SELECTION_CACHE_MAX:
        _SELECTION_CACHE.pop(next(iter(_SELECTION_CACHE)), None)
    _SELECTION_CACHE[qa_id] = selection


@router.get("/{qa_id}/stream")
async def stream(qa_id: str):
    """SSE 流式回推：answer_delta / status / concepts / done。"""
    meta = await _load_meta(qa_id)
    pipe = await _pipeline_for(qa_id, meta["session_id"], meta["question"])
    # 重建场景(刷新/断线重连):material context 不在 DB,按 material_id 现检索注入
    if meta.get("material_id"):
        from app.learning import service as learning_service
        ctx_detail = await learning_service.get_material_context_detail(
            str(meta["material_id"]), meta["question"]
        )
        if ctx_detail.get("context_text"):
            pipe.set_material_context(ctx_detail["context_text"])
    # 就选段提问：注入 drilldown 时暂存的选中原文（一次性语境）
    sel = _SELECTION_CACHE.pop(qa_id, None)
    if sel:
        pipe.set_selection(sel)

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
    diff_hint = None
    if _is_uuid(req.concept_id):
        history = await concept_service.get_concept_history(req.concept_id)
    else:
        history = None
    if history and history.get("explore_count", 0) >= 2:
        contexts = history.get("contexts", [])
        prior_qs = [c["question"] for c in contexts[:3] if c.get("question")]
        if prior_qs:
            diff_hint = (
                f"（你之前在「{'、'.join(prior_qs)}」的语境下了解过【{history.get('canonical_name', '')}】，"
                f"这里请补充它在当前语境下的不同侧面，避免与前述内容重复。）"
            )
    # 下钻问题展开式包装（防回答同质化）：前端概念下钻传的就是概念名两三个字
    # （如"保留集"），光秃秃的概念名当问题，模型最安全的答法是概括上层主题
    # -> 每层回答趋同。包装成显式的概念展开式问句，明确"从该概念本身讲起"。
    # 先包装再拼差异 hint（hint 前置会让包装启发式判定失败）。
    # mode="ask"（针对性提问）跳过包装：用户问句本身已完整，直接作子层问题。
    if req.mode == "ask":
        question = (req.question or "").strip()
    else:
        question = _expand_drill_question(req.question, parent.get("question"))
    if diff_hint:
        question = f"{diff_hint}{question}"
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
    pipe = await _pipeline_for(qa_id, parent["session_id"], question)
    # 就选段提问：暂存选中原文，前端订阅 stream 时注入 prompt（一次性语境）
    try:
        new_pipe = await pipe.drill_down(
            parent_qa_id=qa_id, concept_id=req.concept_id, question=question
        )
    except DepthLimitReached as e:
        # 膨胀降级：超 6 层 -> 标注已有概念，不新建推理
        raise HTTPException(status_code=422, detail=str(e))
    if req.selection and req.selection.strip():
        _remember_selection(new_pipe.qa_id, req.selection.strip())
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


@router.post("/{qa_id}/reask")
async def reask(qa_id: str, req: ReAskRequest):
    """重问：修改问题并 in-place 重新生成本层回答（生成出错/不满意时用）。
    只改问题清现场，不开新节点——树结构、session、material_id 全保留；
    推理由前端重新订阅 stream 触发（run() 开头 reset_answer 幂等）。"""
    question = (req.question or "").strip()
    if not question:
        raise HTTPException(status_code=422, detail="question is empty")
    meta = await _load_meta(qa_id)
    from sqlalchemy.exc import NoResultFound
    try:
        await repo.reask(qa_id, question)
    except NoResultFound:
        raise HTTPException(status_code=404, detail=f"qa_step {qa_id} not found")
    return {"qa_id": qa_id, "question": question, "session_id": meta["session_id"],
            "status": "generating"}


@router.patch("/{qa_id}/checked")
async def set_checked(qa_id: str, checked: bool = Query(...)):
    """手动勾选/取消勾选某层学习完成度（左边栏 check 框）。"""
    from app.db import session_scope, is_sqlite
    from sqlalchemy import update as sa_update
    from app.models.tables import QAStep as _QS
    async with session_scope() as s:
        res = await s.execute(
            sa_update(_QS).where(_QS.qa_id == qa_id).values(checked=checked)
        )
        if res.rowcount == 0:
            raise HTTPException(status_code=404, detail=f"qa_step {qa_id} not found")
    return {"qa_id": qa_id, "checked": checked}


@router.delete("/{qa_id}")
async def delete_qa_step(qa_id: str):
    """删除一个探索层（含全部子层，级联）。

    与 delete_session 同构的清理边界：
    - qa_step 子树走 parent_qa_id 的 ondelete=CASCADE 递归删除；
    - concept_edge.session_id 无外键（纯 UUID 列），按步骤所属 session 手动清
      同源边——层的边数据与层共存亡，跨层共享的边不动；
    - 概念节点本身保留（可复用资产，同"删足迹≠删材料"的边界）。
    删根层（无 parent）等价于删整棵树。
    """
    from app.db import session_scope
    from sqlalchemy import delete as sa_delete, select
    from app.models.tables import QAStep as _QS, ConceptEdge as _CE
    async with session_scope() as s:
        row = (
            await s.execute(
                select(_QS.qa_id, _QS.session_id).where(_QS.qa_id == qa_id)
            )
        ).first()
        if row is None:
            raise HTTPException(status_code=404, detail=f"qa_step {qa_id} not found")
        # 边没有 qa_id 维度,无法精确归属到某层,按会话粒度清理
        await s.execute(sa_delete(_CE).where(_CE.session_id == row.session_id))
        step = await s.get(_QS, qa_id)
        await s.delete(step)
    return {"qa_id": qa_id, "deleted": True}


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
def _expand_drill_question(question: str | None, parent_question: str | None = None) -> str:
    """把光秃秃的概念名包装成概念展开式下钻问句（防回答同质化）。

    前端概念下钻传的 question 就是概念名本身（2-8 字），直接当 prompt 的
    "问题"，模型最安全的答法是概括主题 -> 每层回答趋同、沦为对最高层内容
    的概括性解读。包装后明确要求从概念本身展开、点出与父层的衔接。

    判定：非空、<=24 字、不含问号且不是已包装格式 -> 视为概念名。
    用户完整问句（含问号或较长）原样返回。
    """
    q = (question or "").strip()
    if not q:
        return q
    if len(q) > 24 or "?" in q or "？" in q or q.startswith("深入解释"):
        return q
    # 从父层问题提取父概念名（「X」格式），给"在什么语境下"一个锚点
    import re as _re
    m = _re.search(r"「(.+?)」", parent_question or "")
    if m:
        parent_concept = m.group(1)
    else:
        # 父层是自由问句：剥掉疑问前缀后截断，避免锚点变成"什么是 LLM 评测中的"
        pq = _re.sub(r"^(什么是|请(详细)?(解释|介绍|说明)|如何理解|为什么|详述|解释|介绍)\s*", "",
                     (parent_question or "").strip())
        parent_concept = pq[:12]
    if parent_concept and parent_concept != q:
        return (
            f"深入解释「{q}」：它是什么、核心机制/原理是什么、"
            f"与「{parent_concept}」的关系和在其中的具体角色，并给一个典型示例。"
            f"从「{q}」本身展开，不要概括复述上层主题。"
        )
    return (
        f"深入解释「{q}」：它是什么、核心机制/原理是什么、有什么典型示例，"
        f"以及为什么重要。从「{q}」本身展开，不要概括复述上层主题。"
    )


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
