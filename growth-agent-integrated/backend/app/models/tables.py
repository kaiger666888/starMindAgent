"""ORM 模型，严格对齐 migrations/001_init.sql 与技术架构文档第四节。

注意：
- ConceptNode 是唯一一等公民，同概念多层出现只存引用不存副本，
  explore_count 跨问答轮次累计。
- ConceptEdge 单表 + origin 派生三状态视图，不在 ORM 维护三份图谱。
- QAStep 自引用 parent_qa_id 挂出探索树；version 为乐观锁。
- Audit log 只追加；payload 存合并前快照支持 undo 反向回放。
"""
from __future__ import annotations
import uuid
from datetime import datetime
from sqlalchemy import (
    String, Text, Integer, Float, DateTime, ForeignKey, CheckConstraint, Index, func,
    BigInteger, Boolean, JSON,
)
from sqlalchemy.dialects.postgresql import UUID, JSONB
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship

# sqlite 兼容：JSONB/UUID 是 PG 方言类型，sqlite 编译不了 create_all
_JSONB = JSONB().with_variant(JSON(), "sqlite")
_UUID = UUID(as_uuid=False).with_variant(String(36), "sqlite")


class Base(DeclarativeBase):
    pass


def _uuid() -> str:
    return str(uuid.uuid4())


class ConceptNode(Base):
    __tablename__ = "concept_node"
    concept_id: Mapped[str] = mapped_column(_UUID, primary_key=True, default=_uuid)
    canonical_name: Mapped[str] = mapped_column(Text, nullable=False)
    aliases: Mapped[list] = mapped_column(_JSONB, nullable=False, default=list)
    domain_tag: Mapped[str | None] = mapped_column(Text)
    source: Mapped[str] = mapped_column(Text, nullable=False, default="llm_extracted")
    explore_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    # 004 增强：双计数 + 成熟度（红色语义区分）
    drill_down_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)  # 主动下钻
    visit_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)  # 回访
    understood: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    last_explored_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())

    __table_args__ = (
        CheckConstraint("source IN ('preset','llm_extracted')", name="ck_concept_source"),
    )


class ConceptEdge(Base):
    __tablename__ = "concept_edge"
    edge_id: Mapped[str] = mapped_column(_UUID, primary_key=True, default=_uuid)
    session_id: Mapped[str] = mapped_column(_UUID, nullable=False)
    source_id: Mapped[str] = mapped_column(ForeignKey("concept_node.concept_id", ondelete="CASCADE"))
    target_id: Mapped[str] = mapped_column(ForeignKey("concept_node.concept_id", ondelete="CASCADE"))
    relation_type: Mapped[str] = mapped_column(Text, nullable=False, default="related")
    origin: Mapped[str] = mapped_column(Text, nullable=False, default="user_click")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    __table_args__ = (
        CheckConstraint(
            "origin IN ('user_click','co_occurrence','domain_graph')", name="ck_edge_origin"
        ),
        Index("idx_edge_session_origin", "session_id", "origin"),
    )


class QASession(Base):
    __tablename__ = "qa_session"
    session_id: Mapped[str] = mapped_column(_UUID, primary_key=True, default=_uuid)
    user_id: Mapped[str | None] = mapped_column(Text)
    domain_tag: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class QAStep(Base):
    __tablename__ = "qa_step"
    qa_id: Mapped[str] = mapped_column(_UUID, primary_key=True, default=_uuid)
    session_id: Mapped[str] = mapped_column(ForeignKey("qa_session.session_id", ondelete="CASCADE"))
    parent_qa_id: Mapped[str | None] = mapped_column(ForeignKey("qa_step.qa_id", ondelete="CASCADE"))
    question: Mapped[str] = mapped_column(Text, nullable=False)
    answer: Mapped[str | None] = mapped_column(Text)
    answer_offset: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    extracted_concept_ids: Mapped[list] = mapped_column(_JSONB, nullable=False, default=list)
    status: Mapped[str] = mapped_column(Text, nullable=False, default="generating")
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)  # 乐观锁
    # 埋点字段
    model: Mapped[str | None] = mapped_column(Text)
    prompt_hash: Mapped[str | None] = mapped_column(Text)
    raw_output: Mapped[str | None] = mapped_column(Text)
    parsed_concepts: Mapped[list] = mapped_column(_JSONB, nullable=False, default=list)
    aliases: Mapped[list] = mapped_column(_JSONB, nullable=False, default=list)
    confidence: Mapped[float | None] = mapped_column(Float)
    depth: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    # 004 增强：层摘要 / 放弃标记 / 上次看的概念
    layer_summary: Mapped[str | None] = mapped_column(Text)
    browsed_not_drilled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    last_viewed_concept_id: Mapped[str | None] = mapped_column(
        ForeignKey("concept_node.concept_id", ondelete="SET NULL")
    )
    # 006: 关联学习材料（根层=导入的 md，子层继承用于上下文注入）
    material_id: Mapped[str | None] = mapped_column(
        ForeignKey("learning_material.material_id", ondelete="SET NULL")
    )
    # 007: 学习完成度手动勾选（左边栏 check 框 -> 深绿背景）
    checked: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())

    __table_args__ = (
        CheckConstraint("status IN ('generating','extracting','waiting')", name="ck_qastep_status"),
        Index("idx_qastep_session", "session_id"),
        Index("idx_qastep_parent", "parent_qa_id"),
    )


class AuditLog(Base):
    """归一化决策 + merge/undo 动作，同一通道只追加。

    评测可按 qa_id 回放全部决策，并与 golden set 比对。
    """
    __tablename__ = "audit_log"
    log_id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    qa_id: Mapped[str | None] = mapped_column(ForeignKey("qa_step.qa_id", ondelete="SET NULL"))
    session_id: Mapped[str | None] = mapped_column(ForeignKey("qa_session.session_id", ondelete="CASCADE"))
    # 归一化决策字段
    candidate_name: Mapped[str | None] = mapped_column(Text)
    matched_alias: Mapped[str | None] = mapped_column(Text)
    similarity_score: Mapped[float | None] = mapped_column(Float)
    action: Mapped[str] = mapped_column(Text, nullable=False)  # merge / keep / undo
    llm_verdict: Mapped[str | None] = mapped_column(Text)
    # merge/undo 动作字段
    merge_id: Mapped[str] = mapped_column(_UUID, default=_uuid)
    survivor_id: Mapped[str | None] = mapped_column(ForeignKey("concept_node.concept_id", ondelete="CASCADE"))
    absorbed_id: Mapped[str | None] = mapped_column(ForeignKey("concept_node.concept_id", ondelete="CASCADE"))
    payload: Mapped[dict] = mapped_column(_JSONB, nullable=False, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    __table_args__ = (
        CheckConstraint("action IN ('merge','keep','undo')", name="ck_audit_action"),
        Index("idx_audit_qa", "qa_id"),
        Index("idx_audit_merge", "merge_id"),
        Index("idx_audit_session", "session_id", "created_at"),
    )


class BackfillTask(Base):
    """异步补标注任务，持久化由 worker 池消费，不进内存队列。"""
    __tablename__ = "backfill_task"
    task_id: Mapped[str] = mapped_column(_UUID, primary_key=True, default=_uuid)
    qa_id: Mapped[str] = mapped_column(ForeignKey("qa_step.qa_id", ondelete="CASCADE"))
    status: Mapped[str] = mapped_column(Text, nullable=False, default="pending")
    retry_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    last_error: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())

    __table_args__ = (
        CheckConstraint("status IN ('pending','running','done','dead')", name="ck_backfill_status"),
        Index(
            "idx_backfill_pending", "status", "created_at",
            postgresql_where="status = 'pending'",
        ),
    )


# ---------------------------------------------------------------------------
# 学习记忆层（对应 migrations/003_memory.sql）
# - app_user: 轻量用户主表（外部传入 user_id，免登录）
# - user_profile: LLM 周期性总结的学习画像（JSONB）
# 原始学习数据在 qa_session/qa_step/concept_node，画像在此聚合。
# ---------------------------------------------------------------------------
class AppUser(Base):
    __tablename__ = "app_user"
    user_id: Mapped[str] = mapped_column(Text, primary_key=True)
    display_name: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    last_active_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class UserProfile(Base):
    __tablename__ = "user_profile"
    user_id: Mapped[str] = mapped_column(Text, ForeignKey("app_user.user_id", ondelete="CASCADE"), primary_key=True)
    # LLM 总结的画像：{mastered:[], weak:[], interests:[], recommendation:str, summary:str}
    profile: Mapped[dict] = mapped_column(_JSONB, nullable=False, default=dict)
    qa_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    concept_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    last_summary_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    summary_model: Mapped[str | None] = mapped_column(Text)
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)


# ---------------------------------------------------------------------------
# 学习材料导入（006 迁移）：导入 markdown 作探索根 + 上下文注入
# ---------------------------------------------------------------------------
class LearningMaterial(Base):
    __tablename__ = "learning_material"
    material_id: Mapped[str] = mapped_column(_UUID, primary_key=True, default=_uuid)
    user_id: Mapped[str] = mapped_column(Text, ForeignKey("app_user.user_id", ondelete="CASCADE"), nullable=False)
    title: Mapped[str] = mapped_column(Text, nullable=False)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    content_plain: Mapped[str] = mapped_column(Text, nullable=False)
    size_bytes: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
