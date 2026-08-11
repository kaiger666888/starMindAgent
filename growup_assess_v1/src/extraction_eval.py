"""
概念抽取质量评测
================

对齐 PRD 7.1 / 技术架构文档 9.1:
  - 实体级 Precision / Recall / F1
  - 错误分类: 精确命中 / 幻觉 / 遗漏
  - 匹配方式: canonical_name + aliases 双轨匹配
  - 目标: 经归一化层后 F1 >= 0.80 (基线: 纯 LLM 抽取 63% 准确率)
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

from .models import (
    ErrorType,
    ExtractedConcept,
    GoldenConcept,
    GoldenQA,
    QAStepTelemetry,
)
from .matcher import DualTrackMatcher, ExtractionComparison


@dataclass
class QAEvalResult:
    """单条 QA 的评测结果"""
    qa_id: str
    domain: str
    depth: int
    tp: int = 0
    fp: int = 0
    fn: int = 0
    precision: float = 0.0
    recall: float = 0.0
    f1: float = 0.0
    hallucinated_concepts: list[str] = field(default_factory=list)
    omitted_concepts: list[str] = field(default_factory=list)
    matched_pairs: list[tuple[str, str]] = field(default_factory=list)
    match_type_counts: dict[str, int] = field(default_factory=dict)
    passed: bool = False


@dataclass
class BatchEvalResult:
    """批量评测汇总结果"""
    total_qa: int = 0
    macro_precision: float = 0.0
    macro_recall: float = 0.0
    macro_f1: float = 0.0
    micro_precision: float = 0.0
    micro_recall: float = 0.0
    micro_f1: float = 0.0
    by_domain: dict[str, dict[str, float]] = field(default_factory=dict)
    by_depth: dict[int, dict[str, float]] = field(default_factory=dict)
    total_hallucinations: int = 0
    total_omissions: int = 0
    total_exact_hits: int = 0
    pass_rate: float = 0.0
    per_qa: list[QAEvalResult] = field(default_factory=list)
    gate_f1_threshold: float = 0.80
    gate_passed: bool = False


class ExtractionEvaluator:
    """概念抽取质量评测器"""

    GATE_F1 = 0.80

    def evaluate_qa(
        self,
        golden_qa: GoldenQA,
        predicted: list[ExtractedConcept],
    ) -> QAEvalResult:
        """评测单条 QA 的概念抽取质量。"""
        golden_concepts = golden_qa.golden_concepts
        comparison = DualTrackMatcher.compare(predicted, golden_concepts)

        result = QAEvalResult(
            qa_id=golden_qa.qa_id,
            domain=golden_qa.domain,
            depth=golden_qa.depth,
        )

        result.tp = len(comparison.matched_golden_indices)
        result.fp = sum(1 for i in range(len(predicted))
                        if comparison.pred_to_golden.get(i, -1) == -1)
        result.fn = len(golden_concepts) - result.tp

        for i in range(len(predicted)):
            gold_idx = comparison.pred_to_golden.get(i, -1)
            if gold_idx == -1:
                result.hallucinated_concepts.append(predicted[i].canonical_name)
            else:
                gold = golden_concepts[gold_idx]
                result.matched_pairs.append(
                    (predicted[i].canonical_name, gold.canonical_name)
                )

        for j, gold in enumerate(golden_concepts):
            if j not in comparison.matched_golden_indices:
                result.omitted_concepts.append(gold.canonical_name)

        for detail in comparison.match_details:
            if detail.matched:
                mt = detail.match_type
                result.match_type_counts[mt] = result.match_type_counts.get(mt, 0) + 1

        result.precision = result.tp / (result.tp + result.fp) if (result.tp + result.fp) > 0 else 0.0
        result.recall = result.tp / (result.tp + result.fn) if (result.tp + result.fn) > 0 else 0.0
        pr_sum = result.precision + result.recall
        result.f1 = 2 * result.precision * result.recall / pr_sum if pr_sum > 0 else 0.0

        result.passed = result.f1 >= self.GATE_F1
        return result

    def evaluate_qa_from_telemetry(
        self,
        golden_qa: GoldenQA,
        telemetry: QAStepTelemetry,
    ) -> QAEvalResult:
        """从 QAStep 埋点数据评测（qa_id 回放模式）。"""
        if telemetry.qa_id != golden_qa.qa_id:
            raise ValueError(
                f"qa_id mismatch: golden={golden_qa.qa_id}, telemetry={telemetry.qa_id}"
            )
        return self.evaluate_qa(golden_qa, telemetry.parsed_concepts)

    def evaluate_batch(
        self,
        golden_set: list[GoldenQA],
        predictions_by_qa_id: dict[str, list[ExtractedConcept]],
    ) -> BatchEvalResult:
        """批量评测整个 golden set。"""
        result = BatchEvalResult()
        result.total_qa = len(golden_set)

        global_tp = global_fp = global_fn = 0
        domain_stats: dict[str, dict[str, int]] = {}
        depth_stats: dict[int, dict[str, int]] = {}

        for golden_qa in golden_set:
            qa_id = golden_qa.qa_id
            predicted = predictions_by_qa_id.get(qa_id, [])

            qa_result = self.evaluate_qa(golden_qa, predicted)
            result.per_qa.append(qa_result)

            global_tp += qa_result.tp
            global_fp += qa_result.fp
            global_fn += qa_result.fn
            result.total_hallucinations += qa_result.fp
            result.total_omissions += qa_result.fn
            result.total_exact_hits += qa_result.tp

            domain = golden_qa.domain
            if domain not in domain_stats:
                domain_stats[domain] = {"tp": 0, "fp": 0, "fn": 0}
            domain_stats[domain]["tp"] += qa_result.tp
            domain_stats[domain]["fp"] += qa_result.fp
            domain_stats[domain]["fn"] += qa_result.fn

            depth = golden_qa.depth
            if depth not in depth_stats:
                depth_stats[depth] = {"tp": 0, "fp": 0, "fn": 0}
            depth_stats[depth]["tp"] += qa_result.tp
            depth_stats[depth]["fp"] += qa_result.fp
            depth_stats[depth]["fn"] += qa_result.fn

        if result.per_qa:
            result.macro_precision = sum(r.precision for r in result.per_qa) / len(result.per_qa)
            result.macro_recall = sum(r.recall for r in result.per_qa) / len(result.per_qa)
            pr_sum = result.macro_precision + result.macro_recall
            result.macro_f1 = 2 * result.macro_precision * result.macro_recall / pr_sum if pr_sum > 0 else 0.0

        result.micro_precision = global_tp / (global_tp + global_fp) if (global_tp + global_fp) > 0 else 0.0
        result.micro_recall = global_tp / (global_tp + global_fn) if (global_tp + global_fn) > 0 else 0.0
        micro_pr = result.micro_precision + result.micro_recall
        result.micro_f1 = 2 * result.micro_precision * result.micro_recall / micro_pr if micro_pr > 0 else 0.0

        for domain, stats in domain_stats.items():
            tp, fp, fn = stats["tp"], stats["fp"], stats["fn"]
            p = tp / (tp + fp) if (tp + fp) > 0 else 0.0
            r = tp / (tp + fn) if (tp + fn) > 0 else 0.0
            f1 = 2 * p * r / (p + r) if (p + r) > 0 else 0.0
            result.by_domain[domain] = {"precision": p, "recall": r, "f1": f1, "count": tp + fn}

        for depth, stats in depth_stats.items():
            tp, fp, fn = stats["tp"], stats["fp"], stats["fn"]
            p = tp / (tp + fp) if (tp + fp) > 0 else 0.0
            r = tp / (tp + fn) if (tp + fn) > 0 else 0.0
            f1 = 2 * p * r / (p + r) if (p + r) > 0 else 0.0
            result.by_depth[depth] = {"precision": p, "recall": r, "f1": f1, "count": tp + fn}

        passed_count = sum(1 for r in result.per_qa if r.passed)
        result.pass_rate = passed_count / result.total_qa if result.total_qa > 0 else 0.0
        result.gate_passed = result.micro_f1 >= self.GATE_F1

        return result
