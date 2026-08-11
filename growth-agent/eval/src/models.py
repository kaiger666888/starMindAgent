"""
数据模型 — 与技术架构文档 ConceptNode / QAStep / ConceptEdge 对齐
================================================================

所有评测管线共享的数据结构定义。字段命名严格对应架构文档 §3.1 / §4.1 / §4.2。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Optional


# ── 枚举 ──────────────────────────────────────────────

class ConceptSource(str, Enum):
    PRESET = "preset"
    LLM_EXTRACTED = "llm_extracted"
    HUMAN_ANNOTATED = "human_annotated"   # golden set 专用


class QAStepStatus(str, Enum):
    GENERATING = "generating"
    EXTRACTING = "extracting"
    WAITING = "waiting"


class EdgeOrigin(str, Enum):
    USER_CLICK = "user_click"          # 状态一
    CO_OCCURRENCE = "co_occurrence"    # 状态二
    DOMAIN_GRAPH = "domain_graph"      # 状态三


class NormalizationAction(str, Enum):
    MERGE = "merge"
    KEEP = "keep"


class ErrorType(str, Enum):
    """概念抽取错误分类"""
    EXACT_HIT = "exact_hit"        # 精确命中
    HALLUCINATION = "hallucination"  # 幻觉（抽出了不存在的概念）
    OMISSION = "omission"          # 遗漏（漏抽了关键概念）


# ── 核心数据结构 ──────────────────────────────────────

@dataclass
class ConceptNode:
    """概念节点 — 对齐架构文档 §4.1 ConceptNode"""
    concept_id: str
    canonical_name: str
    aliases: list[str] = field(default_factory=list)
    domain_tag: str = ""
    source: ConceptSource = ConceptSource.LLM_EXTRACTED
    explore_count: int = 0

    def all_names(self) -> set[str]:
        """返回 canonical_name + aliases 的全量名称集合（小写归一化）"""
        names = {self.canonical_name.lower().strip()}
        names.update(a.lower().strip() for a in self.aliases)
        names.discard("")
        return names


@dataclass
class ConceptEdge:
    """概念边 — 对齐架构文档 §4.2 ConceptEdge"""
    edge_id: str
    source_id: str
    target_id: str
    relation_type: str = ""
    origin: EdgeOrigin = EdgeOrigin.CO_OCCURRENCE


@dataclass
class ExtractedConcept:
    """系统抽取的概念（评测输入）"""
    canonical_name: str
    aliases: list[str] = field(default_factory=list)
    confidence: float = 1.0
    raw_text: str = ""               # 原始抽取文本

    def to_concept_node(self, concept_id: str = "", domain_tag: str = "") -> ConceptNode:
        return ConceptNode(
            concept_id=concept_id or f"pred_{id(self)}",
            canonical_name=self.canonical_name,
            aliases=list(self.aliases),
            domain_tag=domain_tag,
            source=ConceptSource.LLM_EXTRACTED,
        )


@dataclass
class GoldenConcept:
    """golden set 中人工标注的正确概念"""
    canonical_name: str
    aliases: list[str] = field(default_factory=list)
    in_answer: bool = True            # 是否确实出现在参考答案中
    note: str = ""                    # 标注备注（如"易与XX混淆"）

    def to_concept_node(self, concept_id: str = "", domain_tag: str = "") -> ConceptNode:
        return ConceptNode(
            concept_id=concept_id or f"golden_{id(self)}",
            canonical_name=self.canonical_name,
            aliases=list(self.aliases),
            domain_tag=domain_tag,
            source=ConceptSource.HUMAN_ANNOTATED,
        )


@dataclass
class GoldenQA:
    """golden set 中的一条 QA（概念抽取评测的基本单元）"""
    qa_id: str
    domain: str
    depth: int = 1                        # 探索树深度（1=根问题）
    parent_concept_chain: list[str] = field(default_factory=list)  # 父概念链
    question: str = ""
    reference_answer: str = ""
    golden_concepts: list[GoldenConcept] = field(default_factory=list)
    tags: list[str] = field(default_factory=list)  # 如 ["alias_heavy", "cross_lang", "depth_sensitive"]


@dataclass
class QAStepTelemetry:
    """QAStep 埋点数据 — 对齐架构文档 §3.3，评测回放依赖"""
    qa_id: str
    session_id: str
    model: str
    prompt_hash: str
    raw_output: str                       # 模型原始输出（含 sentinel 分割的正文+JSON）
    answer_text: str                      # 解析后的正文
    parsed_concepts: list[ExtractedConcept] = field(default_factory=list)
    confidence: float = 0.0
    depth: int = 1
    parent_qa_id: Optional[str] = None
    parent_concept_chain: list[str] = field(default_factory=list)
    timestamp: str = ""


@dataclass
class NormalizationDecision:
    """归一化决策日志 — 对齐架构文档 §3.3"""
    qa_id: str
    candidate_name: str                    # 待归一化的概念名
    matched_alias: str = ""                # 匹配到的别名（精确匹配时）
    similarity_score: float = 0.0          # embedding 相似度
    action: NormalizationAction = NormalizationAction.KEEP
    llm_verdict: str = ""                  # LLM 判定理由（灰区判定时）
    stage: str = ""                        # "alias_exact" / "embedding" / "llm_gray"
    timestamp: str = ""


@dataclass
class GoldenClusterPair:
    """归一化评测的概念对 — 应合并 / 不应合并"""
    pair_id: str
    concept_a: str                         # canonical_name 或别名
    concept_b: str
    should_merge: bool                     # True=应合并, False=不应合并
    difficulty: str = "normal"             # easy / normal / hard
    note: str = ""


@dataclass
class DrilldownEvent:
    """下钻行为信号"""
    qa_id: str
    parent_qa_id: str
    drilled_concept: str
    time_to_back: float = 0.0              # 下钻后到回上层的秒数（0=未回上层）
    immediately_back: bool = False         # 是否立即回上层（< 3s 视为立即）
    judge_scores: dict[str, float] = field(default_factory=dict)  # rubric 各维度得分


@dataclass
class NonFunctionalMetrics:
    """非功能指标快照"""
    streaming_recovery_success_rate: float = 0.0    # 流式中断恢复成功率
    async_backfill_completion_rate: float = 0.0     # 异步补标注完成率
    dead_letter_rate: float = 0.0                   # dead letter 比例
    backfill_p95_latency_ms: float = 0.0            # 回填 P95 延迟 (ms)
    circuit_breaker_trigger_rate: float = 0.0       # 熔断触发率 (次/h)
    sample_count: int = 0                           # 采样窗口内的样本数
    time_window: str = ""                           # 采样时间窗口描述
