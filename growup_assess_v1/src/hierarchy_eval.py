"""
层级累积质量专项评测
====================

对齐 PRD 7.4 / 技术架构文档 9.1 callout:
  质量级联放大风险: 第 1 层概念抽取错误会沿探索树传播到所有子层。
  评测需做"层级累积质量"专项: 在第 3-5 层深度单独采样评测。

策略:
  1. golden set 中包含 depth=3/4/5 的 QA 对
  2. 对每个深度层单独计算 P/R/F1
  3. 计算质量衰减率 (depth N vs depth 1 的 F1 差值)
  4. 门禁: depth 3-5 的 F1 不低于 depth 1 的 80%
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

from .extraction_eval import ExtractionEvaluator, BatchEvalResult, QAEvalResult
from .models import GoldenQA, ExtractedConcept


@dataclass
class HierarchyEvalResult:
    """层级累积质量评测结果"""
    # 各深度层的指标
    by_depth: dict[int, dict[str, float]] = field(default_factory=dict)
    # 质量衰减分析
    depth_1_f1: float = 0.0
    depth_3_5_avg_f1: float = 0.0
    decay_rate: float = 0.0              # F1 衰减率 = (depth_1_f1 - depth_3_5_avg_f1) / depth_1_f1
    # 层级传播分析
    propagated_error_rate: float = 0.0   # 父层错误传播到子层的比例
    # 门禁
    gate_decay_max: float = 0.20         # F1 衰减率不超过 20%
    gate_passed: bool = False
    # 采样信息
    sample_counts: dict[int, int] = field(default_factory=dict)


class HierarchyEvaluator:
    """层级累积质量评测器"""

    TARGET_DEPTHS = [3, 4, 5]            # 专项采样深度
    GATE_DECAY_MAX = 0.20                # F1 衰减率门禁

    def __init__(self, extraction_evaluator: Optional[ExtractionEvaluator] = None):
        self.extraction_eval = extraction_evaluator or ExtractionEvaluator()

    def evaluate(
        self,
        golden_set: list[GoldenQA],
        predictions_by_qa_id: dict[str, list[ExtractedConcept]],
    ) -> HierarchyEvalResult:
        """
        评测层级累积质量。

        对 golden set 中 depth=3/4/5 的 QA 单独采样评测，
        并与 depth=1 的基线对比，计算质量衰减率。

        Args:
            golden_set: 完整 golden set（含各深度层）
            predictions_by_qa_id: {qa_id: [预测概念列表]}

        Returns:
            HierarchyEvalResult: 各深度指标 + 衰减分析 + 门禁
        """
        result = HierarchyEvalResult()

        # 按深度分组
        by_depth_qa: dict[int, list[GoldenQA]] = {}
        for qa in golden_set:
            by_depth_qa.setdefault(qa.depth, []).append(qa)

        # 逐深度评测
        depth_f1_map: dict[int, float] = {}
        for depth, qa_list in sorted(by_depth_qa.items()):
            batch_result = self.extraction_eval.evaluate_batch(
                qa_list,
                {qa_id: preds for qa_id, preds in predictions_by_qa_id.items()
                 if any(q.qa_id == qa_id for q in qa_list)},
            )
            f1 = batch_result.micro_f1
            depth_f1_map[depth] = f1
            result.by_depth[depth] = {
                "precision": batch_result.micro_precision,
                "recall": batch_result.micro_recall,
                "f1": f1,
                "qa_count": batch_result.total_qa,
                "hallucination_count": batch_result.total_hallucinations,
                "omission_count": batch_result.total_omissions,
            }
            result.sample_counts[depth] = batch_result.total_qa

        # 基线 F1 (depth 1)
        result.depth_1_f1 = depth_f1_map.get(1, 0.0)

        # depth 3-5 平均 F1
        target_f1s = [depth_f1_map[d] for d in self.TARGET_DEPTHS if d in depth_f1_map]
        result.depth_3_5_avg_f1 = sum(target_f1s) / len(target_f1s) if target_f1s else 0.0

        # 衰减率
        if result.depth_1_f1 > 0:
            result.decay_rate = (result.depth_1_f1 - result.depth_3_5_avg_f1) / result.depth_1_f1

        # 传播错误率估算: depth N 的遗漏概念中，有多少来自父层概念链的错误抽取
        # 简化估算: 如果父概念链中的概念在 depth N 的 golden concepts 中也出现但被遗漏，
        # 视为传播错误
        propagated_errors = 0
        total_depth_errors = 0
        for depth in self.TARGET_DEPTHS:
            if depth not in by_depth_qa:
                continue
            for qa in by_depth_qa[depth]:
                preds = predictions_by_qa_id.get(qa.qa_id, [])
                qa_result = self.extraction_eval.evaluate_qa(qa, preds)
                # 检查遗漏的概念是否在父概念链中
                parent_chain_set = {c.lower().strip() for c in qa.parent_concept_chain}
                for omitted in qa_result.omitted_concepts:
                    total_depth_errors += 1
                    # 简化: 如果遗漏概念的别名出现在父链中，视为传播错误
                    omitted_lower = omitted.lower().strip()
                    if any(omitted_lower in parent_chain_set or pc in omitted_lower for pc in parent_chain_set):
                        propagated_errors += 1

        result.propagated_error_rate = (
            propagated_errors / total_depth_errors if total_depth_errors > 0 else 0.0
        )

        # 门禁
        result.gate_passed = result.decay_rate <= self.GATE_DECAY_MAX

        return result
