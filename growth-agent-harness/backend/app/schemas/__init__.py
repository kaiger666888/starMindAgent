"""请求 / 响应 schema。

ConceptBlock 是「单次调用流式正文 + 尾部结构化 JSON」协议中尾部 JSON 的契约，
QAStep 状态机消费它解析概念。推理框架通过约束解码（outlines/xgrammar）保证产出符合该 schema。
"""
from __future__ import annotations
from typing import Literal, Optional
from pydantic import BaseModel, Field


class ConceptItem(BaseModel):
    """单次抽取出的一个概念候选项。"""
    name: str = Field(..., description="概念名，待归一化")
    aliases: list[str] = Field(default_factory=list, description="该概念的中英文/缩写别名")
    confidence: float = Field(0.0, ge=0.0, le=1.0, description="抽取置信度")
    relation_type: str = Field("related", description="与父概念的关系类型")


class ConceptBlock(BaseModel):
    """尾部结构化 JSON 契约（sentinel 之后）。

    技术架构文档 6.1：模型先输出自然语言回答，以 sentinel 分隔，后接本结构化 JSON。
    """
    concepts: list[ConceptItem] = Field(default_factory=list)
    model: str = "unknown"
    prompt_hash: Optional[str] = None


# —— QAStep 相关 ——
class QAStartRequest(BaseModel):
    session_id: Optional[str] = None  # 不给则新建会话
    question: str
    domain_tag: Optional[str] = None
    parent_qa_id: Optional[str] = None  # 下钻时挂父 QAStep


class QAStepOut(BaseModel):
    qa_id: str
    session_id: str
    parent_qa_id: Optional[str]
    question: str
    answer: Optional[str]
    status: str
    version: int
    depth: int
    extracted_concept_ids: list[str] = []
    confidence: Optional[float] = None


class DriftDownRequest(BaseModel):
    """点击概念下钻：fork 新 QAStep，挂 parent_qa_id。"""
    concept_id: str
    question: Optional[str] = None  # 不给则用概念 canonical_name 作为问题


class RollbackRequest(BaseModel):
    """回上层：栈式回退，状态保留。"""
    target_qa_id: str  # 回到哪一层


# —— Concept 服务接口 ——
class MergeRequest(BaseModel):
    id_a: str
    id_b: str


class UndoMergeRequest(BaseModel):
    merge_id: str


class GraphRequest(BaseModel):
    session_id: str
    origin_filter: Optional[list[Literal["user_click", "co_occurrence", "domain_graph"]]] = None


class ExploreRequest(BaseModel):
    concept_id: str
