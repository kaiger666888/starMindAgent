"""
评测报告生成器
==============

汇总所有评测维度的结果，生成结构化评测报告。
支持 Markdown 和 JSON 两种输出格式。

对齐任务交付要求:
  - 自动化评测脚本 + 报告模板
  - 支持 qa_id 回放比对 golden set
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional

from .extraction_eval import BatchEvalResult
from .normalization_eval import NormalizationEvalResult
from .drilldown_eval import DrilldownEvalResult
from .hierarchy_eval import HierarchyEvalResult
from .nonfunctional_eval import NonFunctionalGateResult


@dataclass
class EvaluationReport:
    """完整评测报告"""
    # 元信息
    report_id: str = ""
    generated_at: str = ""
    evaluator: str = "AI 评测工程师"
    golden_set_version: str = "v1.0"
    # 各维度结果
    extraction_result: Optional[BatchEvalResult] = None
    normalization_result: Optional[NormalizationEvalResult] = None
    drilldown_result: Optional[DrilldownEvalResult] = None
    hierarchy_result: Optional[HierarchyEvalResult] = None
    nonfunctional_result: Optional[NonFunctionalGateResult] = None
    # 总体结论
    overall_passed: bool = False
    summary: str = ""

    def to_markdown(self) -> str:
        """生成 Markdown 格式报告"""
        lines = []
        lines.append(f"# 「伴你成长」学习 Agent — 概念抽取评测报告")
        lines.append("")
        lines.append(f"**报告 ID**: {self.report_id}")
        lines.append(f"**生成时间**: {self.generated_at}")
        lines.append(f"**评测人**: {self.evaluator}")
        lines.append(f"**Golden Set 版本**: {self.golden_set_version}")
        lines.append("")
        lines.append("---")
        lines.append("")

        # 总体结论
        lines.append("## 总体结论")
        lines.append("")
        status = "PASS" if self.overall_passed else "FAIL"
        lines.append(f"**总体门禁状态**: {status}")
        lines.append("")
        lines.append(self.summary if self.summary else "（详见各维度分析）")
        lines.append("")

        # 各维度门禁概览
        lines.append("### 门禁概览")
        lines.append("")
        lines.append("| 维度 | 门禁 | 结果 |")
        lines.append("|------|------|------|")

        if self.extraction_result:
            ext_status = "PASS" if self.extraction_result.gate_passed else "FAIL"
            lines.append(f"| 概念抽取质量 | F1 >= 0.80 | {ext_status} (micro F1={self.extraction_result.micro_f1:.4f}) |")

        if self.normalization_result:
            norm_status = "PASS" if self.normalization_result.gate_passed else "FAIL"
            lines.append(f"| 概念归一化 | 误合并率<5%, 合并R>85% | {norm_status} (误合并={self.normalization_result.false_merge_rate:.2%}, R={self.normalization_result.merge_recall:.2%}) |")

        if self.drilldown_result:
            dd_status = "PASS" if self.drilldown_result.gate_passed else "FAIL"
            lines.append(f"| 下钻有效性 | 均分>=3.5, 立即回退<15% | {dd_status} (均分={self.drilldown_result.avg_overall:.2f}, 回退率={self.drilldown_result.immediate_back_rate:.2%}) |")

        if self.hierarchy_result:
            hie_status = "PASS" if self.hierarchy_result.gate_passed else "FAIL"
            lines.append(f"| 层级累积质量 | F1衰减<=20% | {hie_status} (衰减={self.hierarchy_result.decay_rate:.2%}) |")

        if self.nonfunctional_result:
            nf_status = "PASS" if self.nonfunctional_result.all_passed else "FAIL"
            lines.append(f"| 非功能指标 | 4项硬门禁 | {nf_status} |")

        lines.append("")

        # 1. 概念抽取质量
        if self.extraction_result:
            r = self.extraction_result
            lines.append("---")
            lines.append("")
            lines.append("## 1. 概念抽取质量")
            lines.append("")
            lines.append("### 1.1 总体指标")
            lines.append("")
            lines.append(f"- **Micro Precision**: {r.micro_precision:.4f}")
            lines.append(f"- **Micro Recall**: {r.micro_recall:.4f}")
            lines.append(f"- **Micro F1**: {r.micro_f1:.4f}")
            lines.append(f"- **Macro Precision**: {r.macro_precision:.4f}")
            lines.append(f"- **Macro Recall**: {r.macro_recall:.4f}")
            lines.append(f"- **Macro F1**: {r.macro_f1:.4f}")
            lines.append(f"- **门禁 (F1 >= 0.80)**: {'PASS' if r.gate_passed else 'FAIL'}")
            lines.append(f"- **通过率**: {r.pass_rate:.2%} ({sum(1 for x in r.per_qa if x.passed)}/{r.total_qa})")
            lines.append("")

            lines.append("### 1.2 错误分类统计")
            lines.append("")
            lines.append(f"- **精确命中 (TP)**: {r.total_exact_hits}")
            lines.append(f"- **幻觉 (FP)**: {r.total_hallucinations}")
            lines.append(f"- **遗漏 (FN)**: {r.total_omissions}")
            lines.append("")

            lines.append("### 1.3 按领域分组")
            lines.append("")
            lines.append("| 领域 | Precision | Recall | F1 | QA 数 |")
            lines.append("|------|-----------|--------|----|-------|")
            for domain, stats in sorted(r.by_domain.items()):
                lines.append(f"| {domain} | {stats['precision']:.4f} | {stats['recall']:.4f} | {stats['f1']:.4f} | {int(stats['count'])} |")
            lines.append("")

            lines.append("### 1.4 按深度分组")
            lines.append("")
            lines.append("| 深度 | Precision | Recall | F1 | QA 数 |")
            lines.append("|------|-----------|--------|----|-------|")
            for depth in sorted(r.by_depth.keys()):
                stats = r.by_depth[depth]
                lines.append(f"| 第{depth}层 | {stats['precision']:.4f} | {stats['recall']:.4f} | {stats['f1']:.4f} | {int(stats['count'])} |")
            lines.append("")

            # 失败 case
            failed_qa = [x for x in r.per_qa if not x.passed]
            if failed_qa:
                lines.append("### 1.5 未通过门禁的 QA（前 10 条）")
                lines.append("")
                lines.append("| qa_id | 领域 | 深度 | F1 | 幻觉 | 遗漏 |")
                lines.append("|-------|------|------|----|------|------|")
                for x in failed_qa[:10]:
                    hall = ", ".join(x.hallucinated_concepts[:3]) or "-"
                    omit = ", ".join(x.omitted_concepts[:3]) or "-"
                    lines.append(f"| {x.qa_id} | {x.domain} | {x.depth} | {x.f1:.4f} | {hall} | {omit} |")
                lines.append("")

        # 2. 概念归一化
        if self.normalization_result:
            r = self.normalization_result
            lines.append("---")
            lines.append("")
            lines.append("## 2. 概念归一化准确率")
            lines.append("")
            lines.append(f"- **合并 Precision**: {r.merge_precision:.4f}")
            lines.append(f"- **合并 Recall**: {r.merge_recall:.4f}")
            lines.append(f"- **误合并率**: {r.false_merge_rate:.2%} (门禁 < 5%)")
            lines.append(f"- **漏合并率**: {r.miss_merge_rate:.2%}")
            lines.append(f"- **门禁**: {'PASS' if r.gate_passed else 'FAIL'}")
            lines.append(f"- **评测对数**: {r.total_pairs} (应合并 {r.should_merge_total}, 不应合并 {r.should_not_merge_total})")
            lines.append("")

            if r.by_difficulty:
                lines.append("### 2.1 按难度分组")
                lines.append("")
                lines.append("| 难度 | 合并P | 合并R | 误合并率 | 总数 |")
                lines.append("|------|-------|-------|---------|------|")
                for diff, stats in sorted(r.by_difficulty.items()):
                    lines.append(f"| {diff} | {stats['merge_precision']:.4f} | {stats['merge_recall']:.4f} | {stats['false_merge_rate']:.2%} | {int(stats['total'])} |")
                lines.append("")

            if r.false_merges:
                lines.append("### 2.2 误合并案例（前 10 条）")
                lines.append("")
                for fm in r.false_merges[:10]:
                    lines.append(f"- `{fm['concept_a']}` vs `{fm['concept_b']}` (难度: {fm['difficulty']}) — {fm.get('note', '')}")
                lines.append("")

        # 3. 下钻有效性
        if self.drilldown_result:
            r = self.drilldown_result
            lines.append("---")
            lines.append("")
            lines.append("## 3. 下钻有效性")
            lines.append("")
            lines.append("### 3.1 LLM-as-judge 评分")
            lines.append("")
            lines.append(f"- **概念相关性 (均值)**: {r.avg_concept_relevance:.2f} / 5")
            lines.append(f"- **解释准确性 (均值)**: {r.avg_explanation_accuracy:.2f} / 5")
            lines.append(f"- **深度适配性 (均值)**: {r.avg_depth_adaptability:.2f} / 5")
            lines.append(f"- **上下文连贯性 (均值)**: {r.avg_context_coherence:.2f} / 5")
            lines.append(f"- **加权总分 (均值)**: {r.avg_overall:.2f} / 5")
            lines.append(f"- **门禁 (均分 >= 3.5)**: {'PASS' if r.avg_overall >= 3.5 else 'FAIL'}")
            lines.append("")

            lines.append("### 3.2 行为信号")
            lines.append("")
            lines.append(f"- **下钻后立即回上层比例**: {r.immediate_back_rate:.2%} (门禁 < 15%)")
            lines.append(f"- **平均下钻停留时间**: {r.avg_time_to_back:.1f}s")
            lines.append(f"- **总下钻次数**: {r.total_drilldowns}")
            lines.append("")

            if r.score_distribution:
                lines.append("### 3.3 评分分布")
                lines.append("")
                lines.append("| 分数 | 数量 |")
                lines.append("|------|------|")
                for score in sorted(r.score_distribution.keys(), reverse=True):
                    lines.append(f"| {score} 分 | {r.score_distribution[score]} |")
                lines.append("")

        # 4. 层级累积质量
        if self.hierarchy_result:
            r = self.hierarchy_result
            lines.append("---")
            lines.append("")
            lines.append("## 4. 层级累积质量专项")
            lines.append("")
            lines.append(f"- **第 1 层 F1 (基线)**: {r.depth_1_f1:.4f}")
            lines.append(f"- **第 3-5 层平均 F1**: {r.depth_3_5_avg_f1:.4f}")
            lines.append(f"- **F1 衰减率**: {r.decay_rate:.2%} (门禁 <= 20%)")
            lines.append(f"- **传播错误率**: {r.propagated_error_rate:.2%}")
            lines.append(f"- **门禁**: {'PASS' if r.gate_passed else 'FAIL'}")
            lines.append("")

            if r.by_depth:
                lines.append("### 4.1 各深度层指标")
                lines.append("")
                lines.append("| 深度 | Precision | Recall | F1 | QA数 | 幻觉 | 遗漏 |")
                lines.append("|------|-----------|--------|----|------|------|------|")
                for depth in sorted(r.by_depth.keys()):
                    s = r.by_depth[depth]
                    lines.append(f"| 第{depth}层 | {s['precision']:.4f} | {s['recall']:.4f} | {s['f1']:.4f} | {int(s['qa_count'])} | {int(s['hallucination_count'])} | {int(s['omission_count'])} |")
                lines.append("")

        # 5. 非功能指标
        if self.nonfunctional_result:
            r = self.nonfunctional_result
            lines.append("---")
            lines.append("")
            lines.append("## 5. 非功能指标门禁")
            lines.append("")
            lines.append(f"- **采样窗口**: {r.time_window}")
            lines.append(f"- **样本数**: {r.sample_count}")
            lines.append(f"- **总体门禁**: {'PASS' if r.all_passed else 'FAIL'}")
            lines.append("")

            lines.append("### 5.1 逐项检查")
            lines.append("")
            lines.append("| 指标 | 值 | 门禁 | 结果 |")
            lines.append("|------|----|------|------|")
            for name, check in r.checks.items():
                status = "PASS" if check.get("passed", check.get("alert_triggered", False) is False) else ("ALERT" if "alert_triggered" in check else "FAIL")
                val = check.get("value", check.get("value_s", check.get("value_per_hour", "")))
                if isinstance(val, float):
                    if val < 1:
                        val_str = f"{val:.2%}"
                    else:
                        val_str = f"{val:.2f}"
                else:
                    val_str = str(val)
                lines.append(f"| {check['description']} | {val_str} | {check['threshold']} | {status} |")
            lines.append("")

            if r.failed_gates:
                lines.append("### 5.2 未通过项")
                lines.append("")
                for g in r.failed_gates:
                    lines.append(f"- **{g}**")
                lines.append("")

        lines.append("---")
        lines.append("")
        lines.append("## 附录: 评测配置")
        lines.append("")
        lines.append("- 概念抽取门禁: F1 >= 0.80 (基线 63%)")
        lines.append("- 归一化门禁: 误合并率 < 5%, 合并 Recall > 85%")
        lines.append("- 下钻门禁: judge 均分 >= 3.5, 立即回退率 < 15%")
        lines.append("- 层级门禁: F1 衰减率 <= 20%")
        lines.append("- 非功能门禁: 恢复率>95%, 补标注>90%, dead letter<2%, P95<30s, 熔断>10%/h告警")
        lines.append("- 人工校准: 每周抽检 10% 校准 judge 模型")
        lines.append("")

        return "\n".join(lines)

    def to_json(self) -> str:
        """生成 JSON 格式报告"""
        def _safe_dict(obj):
            if obj is None:
                return None
            if hasattr(obj, "__dict__"):
                return {k: _safe_dict(v) for k, v in obj.__dict__.items()}
            if isinstance(obj, dict):
                return {k: _safe_dict(v) for k, v in obj.items()}
            if isinstance(obj, (list, set)):
                return [_safe_dict(v) for v in obj]
            if isinstance(obj, float):
                return round(obj, 4)
            return obj

        return json.dumps(_safe_dict(self), ensure_ascii=False, indent=2)


def generate_report(
    extraction: Optional[BatchEvalResult] = None,
    normalization: Optional[NormalizationEvalResult] = None,
    drilldown: Optional[DrilldownEvalResult] = None,
    hierarchy: Optional[HierarchyEvalResult] = None,
    nonfunctional: Optional[NonFunctionalGateResult] = None,
    golden_set_version: str = "v1.0",
) -> EvaluationReport:
    """生成完整评测报告"""
    report = EvaluationReport(
        report_id=f"eval_{datetime.now().strftime('%Y%m%d_%H%M%S')}",
        generated_at=datetime.now().isoformat(),
        golden_set_version=golden_set_version,
        extraction_result=extraction,
        normalization_result=normalization,
        drilldown_result=drilldown,
        hierarchy_result=hierarchy,
        nonfunctional_result=nonfunctional,
    )

    # 计算总体门禁
    checks = []
    if extraction:
        checks.append(extraction.gate_passed)
    if normalization:
        checks.append(normalization.gate_passed)
    if drilldown:
        checks.append(drilldown.gate_passed)
    if hierarchy:
        checks.append(hierarchy.gate_passed)
    if nonfunctional:
        checks.append(nonfunctional.all_passed)

    report.overall_passed = all(checks) if checks else False

    # 生成摘要
    parts = []
    if extraction:
        parts.append(f"概念抽取 micro F1={extraction.micro_f1:.4f} ({'PASS' if extraction.gate_passed else 'FAIL'})")
    if normalization:
        parts.append(f"归一化误合并率={normalization.false_merge_rate:.2%} ({'PASS' if normalization.gate_passed else 'FAIL'})")
    if drilldown:
        parts.append(f"下钻均分={drilldown.avg_overall:.2f} ({'PASS' if drilldown.gate_passed else 'FAIL'})")
    if hierarchy:
        parts.append(f"层级衰减={hierarchy.decay_rate:.2%} ({'PASS' if hierarchy.gate_passed else 'FAIL'})")
    if nonfunctional:
        parts.append(f"非功能({'PASS' if nonfunctional.all_passed else 'FAIL'})")
    report.summary = " | ".join(parts)

    return report
