"""会话结构化导出：把一次学习（探索树 + 概念图）渲染成可带走的格式。

输出两种格式：
- md   学习手账版式：标题层级表达树的深度（L1->##、L2->###…），
      每层带层摘要引导 + 概念清单，末尾概念附录表。导出的 md 可再导入
      开新探索（与"导入学习文件"闭环）。
- json 机器可读备份：树 + 概念 + 边（含 origin），可用于再导入/评测。

数据聚合与 memory/service.get_session_detail 同源，但额外补：
- layer_summary（层摘要，SessionDetail 未返回）
- 概念 id -> 名字/别名/探索次数映射（从 concept_node join）
- 概念图边（co_occurrence / user_click 两类，domain_graph 是扩展视图不导）
"""
from __future__ import annotations

import logging
from datetime import datetime

from sqlalchemy import select

from app.db import session_scope
from app.models.tables import (
    ConceptEdge, ConceptNode, LearningMaterial, QAStep, QASession,
)

log = logging.getLogger(__name__)

# 标题层级映射：depth 1->##，2->###…封顶 ######（h6）再深不再降级
_MIN_DEPTH = 1
_MAX_LEVEL = 6


async def export_session(session_id: str, fmt: str = "md") -> dict | None:
    """聚合一次学习的完整数据并渲染。

    返回 {"filename", "content", "mime"}；会话不存在返回 None。
    """
    data = await _collect(session_id)
    if data is None:
        return None
    if fmt == "json":
        return {
            "filename": _filename(data["title"], "json"),
            "content": _render_json(data),
            "mime": "application/json",
        }
    return {
        "filename": _filename(data["title"], "md"),
        "content": _render_md(data),
        "mime": "text/markdown",
    }


# ---------------------------------------------------------------------------
# 聚合：一次查询面拉全（steps + 概念 + 边 + 材料）
# ---------------------------------------------------------------------------
async def _collect(session_id: str) -> dict | None:
    async with session_scope() as s:
        sess = (
            await s.execute(select(QASession).where(QASession.session_id == session_id))
        ).scalar_one_or_none()
        if sess is None:
            return None

        steps = (
            await s.execute(
                select(
                    QAStep.qa_id, QAStep.parent_qa_id, QAStep.question,
                    QAStep.answer, QAStep.depth, QAStep.layer_summary,
                    QAStep.extracted_concept_ids, QAStep.material_id,
                    QAStep.created_at, QAStep.status,
                )
                .where(QAStep.session_id == session_id)
                .order_by(QAStep.created_at)
            )
        ).all()

        # 本会话涉及的概念（步骤标注并集 + 图边端点并集）
        concept_ids: set[str] = set()
        for st in steps:
            concept_ids.update(str(c) for c in (st.extracted_concept_ids or []))
        edge_rows = (
            await s.execute(
                select(ConceptEdge).where(
                    ConceptEdge.session_id == session_id,
                    ConceptEdge.origin.in_(["user_click", "co_occurrence"]),
                )
            )
        ).scalars().all()
        for e in edge_rows:
            concept_ids.add(str(e.source_id))
            concept_ids.add(str(e.target_id))
        concepts = []
        if concept_ids:
            rows = (
                await s.execute(
                    select(ConceptNode).where(ConceptNode.concept_id.in_(concept_ids))
                )
            ).scalars().all()
            concepts = rows

        # 根层材料（导入文件会话）
        material_title = None
        material_root = next((st for st in steps if st.parent_qa_id is None), None)
        if material_root is not None and material_root.material_id is not None:
            mat = (
                await s.execute(
                    select(LearningMaterial.title)
                    .where(LearningMaterial.material_id == material_root.material_id)
                )
            ).scalar_one_or_none()
            material_title = mat

    # 会话标题：导入文件用材料名；普通会话用根层问题
    root = next((st for st in steps if st.parent_qa_id is None), None)
    title = material_title or (root.question if root else "学习手账")
    created = sess.created_at.isoformat()[:10] if sess.created_at else ""

    return {
        "session_id": str(sess.session_id),
        "title": title,
        "created_at": created,
        "domain_tag": sess.domain_tag,
        "is_imported": sess.domain_tag == "imported",
        "steps": [
            {
                "qa_id": str(st.qa_id),
                "parent_qa_id": str(st.parent_qa_id) if st.parent_qa_id else None,
                "question": st.question,
                "answer": st.answer or "",
                "depth": st.depth,
                "layer_summary": st.layer_summary,
                "concept_ids": [str(c) for c in (st.extracted_concept_ids or [])],
                "created_at": st.created_at.isoformat() if st.created_at else None,
                "status": st.status,
            }
            for st in steps
        ],
        "concepts": [
            {
                "concept_id": str(c.concept_id),
                "canonical_name": c.canonical_name,
                "aliases": c.aliases or [],
                "explore_count": c.explore_count,
                "understood": c.understood,
                "domain_tag": c.domain_tag,
            }
            for c in concepts
        ],
        "edges": [
            {
                "source": str(e.source_id),
                "target": str(e.target_id),
                "relation_type": e.relation_type,
                "origin": e.origin,
            }
            for e in edge_rows
        ],
    }


# ---------------------------------------------------------------------------
# md 渲染：学习手账版式
# ---------------------------------------------------------------------------
def _render_md(data: dict) -> str:
    by_id = {c["concept_id"]: c for c in data["concepts"]}
    title = data["title"]
    n_layers = len(data["steps"])
    n_concepts = len(data["concepts"])
    src = "导入文件" if data["is_imported"] else "提问"

    lines: list[str] = []
    lines.append(f"# {title}")
    lines.append("")
    lines.append(
        f"> {data['created_at']} · {n_layers} 层 · {n_concepts} 个概念 · 来源：{src}"
        + " · 伴你成长学习手账")
    lines.append("")

    # 树：DFS 按探索顺序，兄弟分支之间 --- 分隔
    children_of: dict[str | None, list[dict]] = {}
    for st in data["steps"]:
        children_of.setdefault(st["parent_qa_id"], []).append(st)
    root_steps = children_of.get(None, [])

    def emit_step(st: dict) -> None:
        depth = st["depth"]
        # 导入会话 L0 根层 depth=0，也映射到 ##（不与文档元标题 # 冲突）
        level = max(2, min(depth - _MIN_DEPTH + 2, _MAX_LEVEL))
        lines.append("#" * level + f" {_display_question(st, data)}")
        lines.append("")
        if st["layer_summary"]:
            lines.append(f"> 摘：{st['layer_summary']}")
            lines.append("")
        answer = (st["answer"] or "").strip()
        if answer:
            lines.append(answer)
        else:
            lines.append("（本层未生成回答）")
        lines.append("")
        names = [
            by_id[cid]["canonical_name"]
            for cid in st["concept_ids"]
            if cid in by_id
        ]
        if names:
            lines.append("**本层概念**：" + " · ".join(names))
            lines.append("")
        kids = children_of.get(st["qa_id"], [])
        for i, kid in enumerate(kids):
            if i > 0:
                lines.append("---")
                lines.append("")
            emit_step(kid)

    for st in root_steps:
        emit_step(st)

    # 附录：概念清单表
    if data["concepts"]:
        lines.append("---")
        lines.append("")
        lines.append("## 附录 · 概念清单")
        lines.append("")
        lines.append("| 概念 | 别名 | 探索 | 状态 |")
        lines.append("|------|------|------|------|")
        for c in sorted(
            data["concepts"], key=lambda x: -x["explore_count"]
        ):
            aliases = "、".join(c["aliases"]) if c["aliases"] else "-"
            status = "已理解" if c["understood"] else (
                "已下钻" if c["explore_count"] >= 2 else "遇到过"
            )
            lines.append(
                f"| {c['canonical_name']} | {aliases} | {c['explore_count']} | {status} |"
            )
        lines.append("")

    return "\n".join(lines)


def _display_question(st: dict, data: dict) -> str:
    """层的展示标题：导入根层用 question；下钻层剥「深入解释「X」」包装取 X。"""
    q = st["question"] or ""
    # 下钻包装问句格式「深入解释「X」…」，取内层概念名
    import re

    m = re.search(r"深入解释「(.+?)」", q)
    if m:
        return m.group(1)
    return q if len(q) <= 60 else q[:57] + "…"


# ---------------------------------------------------------------------------
# json 渲染：机器可读备份
# ---------------------------------------------------------------------------
def _render_json(data: dict) -> str:
    import json

    return json.dumps(data, ensure_ascii=False, indent=2)


def _filename(title: str, ext: str) -> str:
    """文件名：标题清洗 + 日期。Windows 非法字符替换。"""
    safe = "".join(
        ch if ch not in '\\/:*?"<>|' and not ch.isspace() else "_"
        for ch in title[:40]
    ).strip("_")
    date = datetime.now().strftime("%Y%m%d")
    return f"{safe}-{date}.{ext}"
