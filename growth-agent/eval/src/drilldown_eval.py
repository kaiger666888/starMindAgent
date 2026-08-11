"""
下钻有效性评测
==============

对齐 PRD 7.3 / 技术架构文档 9.3:
  - LLM-as-judge 5 分制 rubric:
    概念相关性 / 解释准确性 / 深度适配性 / 上下文连贯性
  - 每周人工抽检 10% 校准 judge 模型
  - 行为信号: 监控"下钻后用户立即回上层"比例（下钻无效的隐式信号）
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

from .models import DrilldownEvent


@dataclass
class JudgeRubricScores:
    """LLM-as-judge 5 分制 rubric 评分"""
    concept_relevance: float = 0.0       # 概念相关性 (1-5)
    explanation_accuracy: float = 0.0    # 解释准确性 (1-5)
    depth_adaptability: float = 0.0      # 深度适配性 (1-5)
    context_coherence: float = 0.0       # 上下文连贯性 (1-5)
    overall: float = 0.0                 # 加权总分
    judge_reasoning: str = ""            # judge 推理过程
    human_calibrated: bool = False       # 是否经过人工校准


@dataclass
class DrilldownEvalResult:
    """下钻有效性评测结果"""
    total_drilldowns: int = 0
    # LLM-as-judge 指标
    avg_concept_relevance: float = 0.0
    avg_explanation_accuracy: float = 0.0
    avg_depth_adaptability: float = 0.0
    avg_context_coherence: float = 0.0
    avg_overall: float = 0.0
    # 行为信号
    immediate_back_rate: float = 0.0     # 下钻后立即回上层比例
    avg_time_to_back: float = 0.0        # 平均下钻停留时间（秒）
    # 质量分布
    score_distribution: dict[str, int] = field(default_factory=dict)  # {"5": 10, "4": 20, ...}
    # 门禁
    gate_min_overall: float = 3.5        # 最低均分门禁
    gate_max_immediate_back: float = 0.15  # 立即回上层比例上限
    gate_passed: bool = False
    # 逐条结果
    per_drilldown: list[dict] = field(default_factory=list)
    # 人工校准
    calibration_sample_count: int = 0
    calibration_agreement_rate: float = 0.0


class DrilldownEvaluator:
    """下钻有效性评测器"""

    RUBRIC_WEIGHTS = {
        "concept_relevance": 0.30,
        "explanation_accuracy": 0.30,
        "depth_adaptability": 0.20,
        "context_coherence": 0.20,
    }

    IMMEDIATE_BACK_THRESHOLD_SEC = 3.0   # < 3s 视为立即回上层

    GATE_MIN_OVERALL = 3.5
    GATE_MAX_IMMEDIATE_BACK = 0.15

    def evaluate(
        self,
        events: list[DrilldownEvent],
        judge_scores_by_qa_id: dict[str, JudgeRubricScores],
    ) -> DrilldownEvalResult:
        """
        评测下钻有效性。

        Args:
            events: 下钻行为事件列表
            judge_scores_by_qa_id: {qa_id: JudgeRubricScores} LLM-as-judge 评分

        Returns:
            DrilldownEvalResult: rubric 均分 + 行为信号 + 门禁判定
        """
        result = DrilldownEvalResult()
        result.total_drilldowns = len(events)

        if not events:
            return result

        overall_scores = []
        immediate_back_count = 0
        time_to_back_list = []

        rubric_sums = {
            "concept_relevance": 0.0,
            "explanation_accuracy": 0.0,
            "depth_adaptability": 0.0,
            "context_coherence": 0.0,
        }
        scored_count = 0

        for event in events:
            # 行为信号
            if event.immediately_back or event.time_to_back < self.IMMEDIATE_BACK_THRESHOLD_SEC:
                immediate_back_count += 1
            if event.time_to_back > 0:
                time_to_back_list.append(event.time_to_back)

            # LLM-as-judge 评分
            scores = judge_scores_by_qa_id.get(event.qa_id)
            entry = {
                "qa_id": event.qa_id,
                "drilled_concept": event.drilled_concept,
                "immediately_back": event.immediately_back,
                "time_to_back": event.time_to_back,
            }

            if scores:
                scored_count += 1
                rubric_sums["concept_relevance"] += scores.concept_relevance
                rubric_sums["explanation_accuracy"] += scores.explanation_accuracy
                rubric_sums["depth_adaptability"] += scores.depth_adaptability
                rubric_sums["context_coherence"] += scores.context_coherence
                overall_scores.append(scores.overall)

                bucket = str(int(scores.overall))
                result.score_distribution[bucket] = result.score_distribution.get(bucket, 0) + 1

                entry["judge_scores"] = {
                    "concept_relevance": scores.concept_relevance,
                    "explanation_accuracy": scores.explanation_accuracy,
                    "depth_adaptability": scores.depth_adaptability,
                    "context_coherence": scores.context_coherence,
                    "overall": scores.overall,
                }

            result.per_drilldown.append(entry)

        # 计算均值
        if scored_count > 0:
            result.avg_concept_relevance = rubric_sums["concept_relevance"] / scored_count
            result.avg_explanation_accuracy = rubric_sums["explanation_accuracy"] / scored_count
            result.avg_depth_adaptability = rubric_sums["depth_adaptability"] / scored_count
            result.avg_context_coherence = rubric_sums["context_coherence"] / scored_count
            result.avg_overall = sum(overall_scores) / scored_count

        # 行为信号
        result.immediate_back_rate = immediate_back_count / result.total_drilldowns
        result.avg_time_to_back = sum(time_to_back_list) / len(time_to_back_list) if time_to_back_list else 0.0

        # 门禁判定
        result.gate_passed = (
            result.avg_overall >= self.GATE_MIN_OVERALL
            and result.immediate_back_rate <= self.GATE_MAX_IMMEDIATE_BACK
        )

        return result

    @staticmethod
    def compute_overall_score(scores: JudgeRubricScores) -> float:
        """计算加权总分"""
        return (
            scores.concept_relevance * DrilldownEvaluator.RUBRIC_WEIGHTS["concept_relevance"]
            + scores.explanation_accuracy * DrilldownEvaluator.RUBRIC_WEIGHTS["explanation_accuracy"]
            + scores.depth_adaptability * DrilldownEvaluator.RUBRIC_WEIGHTS["depth_adaptability"]
            + scores.context_coherence * DrilldownEvaluator.RUBRIC_WEIGHTS["context_coherence"]
        )
