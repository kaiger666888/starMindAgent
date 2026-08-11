"""
概念归一化准确率评测
====================

对齐 PRD 7.2 / 技术架构文档 9.2:
  - Golden concept clusters: 100+ 组已知应合并 / 不应合并的概念对
  - 指标: 合并 Precision / 合并 Recall / 误合并率
  - 门禁: 误合并率 < 5%, 合并 Recall > 85%
  - 误合并比漏合并危害更大（直接污染图谱结构）

依赖: 数据产品经理提供 golden concept clusters（当前未闭合依赖）。
管线框架可先搭建，clusters 到位后接入跑通。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

from .models import GoldenClusterPair, NormalizationAction, NormalizationDecision


@dataclass
class NormalizationEvalResult:
    """归一化评测结果"""
    total_pairs: int = 0
    # 应合并的对
    should_merge_total: int = 0
    should_merge_correct: int = 0        # 系统也判定合并 (TP)
    should_merge_missed: int = 0         # 系统判定不合并 (FN = 漏合并)
    # 不应合并的对
    should_not_merge_total: int = 0
    should_not_merge_correct: int = 0    # 系统也判定不合并 (TN)
    should_not_merge_wrong: int = 0      # 系统判定合并 (FP = 误合并)
    # 指标
    merge_precision: float = 0.0         # 合并 Precision = TP / (TP + FP)
    merge_recall: float = 0.0            # 合并 Recall = TP / (TP + FN)
    false_merge_rate: float = 0.0        # 误合并率 = FP / (FP + TN)
    miss_merge_rate: float = 0.0         # 漏合并率 = FN / (TP + FN)
    # 门禁
    gate_false_merge_max: float = 0.05   # 误合并率 < 5%
    gate_merge_recall_min: float = 0.85  # 合并 Recall > 85%
    gate_passed: bool = False
    # 按难度分组
    by_difficulty: dict[str, dict[str, float]] = field(default_factory=dict)
    # 错误明细
    false_merges: list[dict] = field(default_factory=list)
    missed_merges: list[dict] = field(default_factory=list)


class NormalizationEvaluator:
    """概念归一化准确率评测器"""

    GATE_FALSE_MERGE_MAX = 0.05
    GATE_MERGE_RECALL_MIN = 0.85

    def evaluate(
        self,
        golden_clusters: list[GoldenClusterPair],
        system_decisions: dict[str, NormalizationAction],
    ) -> NormalizationEvalResult:
        """
        评测归一化准确率。

        Args:
            golden_clusters: golden concept clusters（应/不应合并的概念对）
            system_decisions: {pair_id: action(merge/keep)} 系统的归一化决策

        Returns:
            NormalizationEvalResult: 合并 P/R + 误合并率 + 门禁判定
        """
        result = NormalizationEvalResult()
        result.total_pairs = len(golden_clusters)

        diff_stats: dict[str, dict[str, int]] = {}

        for pair in golden_clusters:
            system_action = system_decisions.get(pair.pair_id, NormalizationAction.KEEP)
            system_merged = (system_action == NormalizationAction.MERGE)

            diff = pair.difficulty
            if diff not in diff_stats:
                diff_stats[diff] = {"tp": 0, "fp": 0, "fn": 0, "tn": 0, "total": 0}
            diff_stats[diff]["total"] += 1

            if pair.should_merge:
                result.should_merge_total += 1
                if system_merged:
                    result.should_merge_correct += 1
                    diff_stats[diff]["tp"] += 1
                else:
                    result.should_merge_missed += 1
                    diff_stats[diff]["fn"] += 1
                    result.missed_merges.append({
                        "pair_id": pair.pair_id,
                        "concept_a": pair.concept_a,
                        "concept_b": pair.concept_b,
                        "difficulty": diff,
                        "note": pair.note,
                    })
            else:
                result.should_not_merge_total += 1
                if system_merged:
                    result.should_not_merge_wrong += 1
                    diff_stats[diff]["fp"] += 1
                    result.false_merges.append({
                        "pair_id": pair.pair_id,
                        "concept_a": pair.concept_a,
                        "concept_b": pair.concept_b,
                        "difficulty": diff,
                        "note": pair.note,
                    })
                else:
                    result.should_not_merge_correct += 1
                    diff_stats[diff]["tn"] += 1

        # 计算指标
        tp = result.should_merge_correct
        fp = result.should_not_merge_wrong
        fn = result.should_merge_missed
        tn = result.should_not_merge_correct

        result.merge_precision = tp / (tp + fp) if (tp + fp) > 0 else 0.0
        result.merge_recall = tp / (tp + fn) if (tp + fn) > 0 else 0.0
        result.false_merge_rate = fp / (fp + tn) if (fp + tn) > 0 else 0.0
        result.miss_merge_rate = fn / (tp + fn) if (tp + fn) > 0 else 0.0

        # 按难度分组
        for diff, stats in diff_stats.items():
            tp_d, fp_d, fn_d, tn_d = stats["tp"], stats["fp"], stats["fn"], stats["tn"]
            result.by_difficulty[diff] = {
                "merge_precision": tp_d / (tp_d + fp_d) if (tp_d + fp_d) > 0 else 0.0,
                "merge_recall": tp_d / (tp_d + fn_d) if (tp_d + fn_d) > 0 else 0.0,
                "false_merge_rate": fp_d / (fp_d + tn_d) if (fp_d + tn_d) > 0 else 0.0,
                "total": stats["total"],
            }

        # 门禁判定
        result.gate_passed = (
            result.false_merge_rate < self.GATE_FALSE_MERGE_MAX
            and result.merge_recall > self.GATE_MERGE_RECALL_MIN
        )

        return result

    def evaluate_from_audit_log(
        self,
        golden_clusters: list[GoldenClusterPair],
        audit_decisions: list[NormalizationDecision],
    ) -> NormalizationEvalResult:
        """
        从归一化 audit log 评测。

        audit log 中每条记录包含 candidate_name 和 action，
        需要与 golden cluster pair 做匹配。
        """
        # 构建 pair_id -> action 映射
        # audit log 按 candidate_name 记录，需要与 golden pair 做名称匹配
        system_decisions: dict[str, NormalizationAction] = {}

        for pair in golden_clusters:
            for decision in audit_decisions:
                names = {pair.concept_a.lower().strip(), pair.concept_b.lower().strip()}
                candidate = decision.candidate_name.lower().strip()
                matched_alias = decision.matched_alias.lower().strip() if decision.matched_alias else ""
                if candidate in names or matched_alias in names:
                    system_decisions[pair.pair_id] = decision.action
                    break

        return self.evaluate(golden_clusters, system_decisions)
