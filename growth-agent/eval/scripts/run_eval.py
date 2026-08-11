#!/usr/bin/env python3
"""
评测管线主入口
==============

用法:
  # 概念抽取评测（使用 golden set + 模拟预测）
  python scripts/run_eval.py --mode extraction --golden-set golden_set/

  # 归一化评测（需 golden concept clusters）
  python scripts/run_eval.py --mode normalization --clusters clusters.json

  # 下钻有效性评测（需 LLM judge + 行为日志）
  python scripts/run_eval.py --mode drilldown --events events.json

  # 层级累积质量评测
  python scripts/run_eval.py --mode hierarchy --golden-set golden_set/

  # 非功能指标门禁（需 Harness 指标接口）
  python scripts/run_eval.py --mode nonfunctional --metrics metrics.json

  # 全量评测报告
  python scripts/run_eval.py --mode full --golden-set golden_set/ --output report.md

  # qa_id 回放比对
  python scripts/run_eval.py --mode replay --golden-set golden_set/ --telemetry telemetry.json
"""

import argparse
import json
import os
import sys

# 添加项目根目录到 path
PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, PROJECT_ROOT)

from src.extraction_eval import ExtractionEvaluator
from src.normalization_eval import NormalizationEvaluator
from src.drilldown_eval import DrilldownEvaluator, JudgeRubricScores
from src.hierarchy_eval import HierarchyEvaluator
from src.nonfunctional_eval import NonFunctionalEvaluator
from src.report_generator import generate_report
from src.replay import ReplayComparator
from src.models import (
    GoldenQA, GoldenConcept, ExtractedConcept,
    GoldenClusterPair, NormalizationAction,
    DrilldownEvent, NonFunctionalMetrics,
    QAStepTelemetry,
)


def load_golden_set(path: str) -> list[GoldenQA]:
    """加载 golden set 目录下所有 JSON"""
    golden_set = []
    for fname in os.listdir(path):
        if not fname.endswith(".json") or fname == "schema.json":
            continue
        fpath = os.path.join(path, fname)
        with open(fpath, "r", encoding="utf-8") as f:
            data = json.load(f)
        qa_list = data.get("qa_pairs", []) if isinstance(data, dict) else data
        for qa_data in qa_list:
            golden_concepts = [
                GoldenConcept(
                    canonical_name=gc["canonical_name"],
                    aliases=gc.get("aliases", []),
                    in_answer=gc.get("in_answer", True),
                    note=gc.get("note", ""),
                )
                for gc in qa_data.get("golden_concepts", [])
            ]
            golden_set.append(GoldenQA(
                qa_id=qa_data["qa_id"],
                domain=qa_data.get("domain", ""),
                depth=qa_data.get("depth", 1),
                parent_concept_chain=qa_data.get("parent_concept_chain", []),
                question=qa_data.get("question", ""),
                reference_answer=qa_data.get("reference_answer", ""),
                golden_concepts=golden_concepts,
                tags=qa_data.get("tags", []),
            ))
    return golden_set


def run_extraction_eval(golden_set_path: str, output: str = None):
    """概念抽取评测"""
    golden_set = load_golden_set(golden_set_path)
    print(f"Loaded {len(golden_set)} golden QA pairs")

    # 生成模拟预测（实际使用时替换为系统输出）
    # 这里用 golden concepts 的 canonical_name 作为"完美预测"来验证管线
    predictions = {}
    for qa in golden_set:
        predictions[qa.qa_id] = [
            ExtractedConcept(
                canonical_name=gc.canonical_name,
                aliases=gc.aliases,
                confidence=1.0,
            )
            for gc in qa.golden_concepts
        ]

    evaluator = ExtractionEvaluator()
    result = evaluator.evaluate_batch(golden_set, predictions)

    print(f"\n=== 概念抽取质量评测 ===")
    print(f"Micro Precision: {result.micro_precision:.4f}")
    print(f"Micro Recall:    {result.micro_recall:.4f}")
    print(f"Micro F1:        {result.micro_f1:.4f}")
    print(f"Macro F1:        {result.macro_f1:.4f}")
    print(f"门禁 (F1>=0.80): {'PASS' if result.gate_passed else 'FAIL'}")
    print(f"通过率: {result.pass_rate:.2%}")
    print(f"幻觉: {result.total_hallucinations}, 遗漏: {result.total_omissions}")
    print(f"\n按领域:")
    for domain, stats in sorted(result.by_domain.items()):
        print(f"  {domain}: F1={stats['f1']:.4f} ({int(stats['count'])} QA)")
    print(f"\n按深度:")
    for depth, stats in sorted(result.by_depth.items()):
        print(f"  第{depth}层: F1={stats['f1']:.4f} ({int(stats['count'])} QA)")

    return result


def run_hierarchy_eval(golden_set_path: str):
    """层级累积质量评测"""
    golden_set = load_golden_set(golden_set_path)
    print(f"Loaded {len(golden_set)} golden QA pairs")

    predictions = {}
    for qa in golden_set:
        predictions[qa.qa_id] = [
            ExtractedConcept(canonical_name=gc.canonical_name, aliases=gc.aliases)
            for gc in qa.golden_concepts
        ]

    evaluator = HierarchyEvaluator()
    result = evaluator.evaluate(golden_set, predictions)

    print(f"\n=== 层级累积质量专项 ===")
    print(f"第1层 F1 (基线): {result.depth_1_f1:.4f}")
    print(f"第3-5层平均 F1:  {result.depth_3_5_avg_f1:.4f}")
    print(f"F1 衰减率:       {result.decay_rate:.2%}")
    print(f"传播错误率:      {result.propagated_error_rate:.2%}")
    print(f"门禁 (衰减<=20%): {'PASS' if result.gate_passed else 'FAIL'}")
    print(f"\n各深度层:")
    for depth, stats in sorted(result.by_depth.items()):
        print(f"  第{depth}层: F1={stats['f1']:.4f}, QA数={int(stats['qa_count'])}")

    return result


def run_nonfunctional_eval(metrics_path: str = None):
    """非功能指标门禁评测"""
    # 使用模拟数据（实际使用时从 Harness 接口获取）
    metrics = NonFunctionalMetrics(
        streaming_recovery_success_rate=0.97,
        async_backfill_completion_rate=0.93,
        dead_letter_rate=0.015,
        backfill_p95_latency_ms=25000,
        circuit_breaker_trigger_rate=8.0,
        sample_count=1000,
        time_window="2026-08-05 10:00-11:00",
    )

    evaluator = NonFunctionalEvaluator()
    result = evaluator.evaluate(metrics)

    print(f"\n=== 非功能指标门禁 ===")
    print(f"采样窗口: {result.time_window}")
    print(f"样本数: {result.sample_count}")
    print(f"总体门禁: {'PASS' if result.all_passed else 'FAIL'}")
    print(f"\n逐项检查:")
    for name, check in result.checks.items():
        status = "PASS" if check.get("passed", not check.get("alert_triggered", False)) else ("ALERT" if "alert_triggered" in check else "FAIL")
        val = check.get("value", check.get("value_s", check.get("value_per_hour", "")))
        if isinstance(val, float) and val < 1:
            val_str = f"{val:.2%}"
        elif isinstance(val, float):
            val_str = f"{val:.2f}"
        else:
            val_str = str(val)
        print(f"  {check['description']}: {val_str} | 门禁: {check['threshold']} | {status}")

    return result


def run_replay(golden_set_path: str, telemetry_path: str = None):
    """qa_id 回放比对"""
    comparator = ReplayComparator(golden_set_path)
    golden_ids = comparator.list_golden_qa_ids()
    print(f"Golden set 中有 {len(golden_ids)} 个 qa_id")

    # 使用 golden concepts 作为模拟埋点
    if telemetry_path and os.path.exists(telemetry_path):
        with open(telemetry_path, "r") as f:
            telemetry_data = json.load(f)
        telemetries = [
            QAStepTelemetry(
                qa_id=t["qa_id"],
                session_id=t.get("session_id", ""),
                model=t.get("model", ""),
                prompt_hash=t.get("prompt_hash", ""),
                raw_output=t.get("raw_output", ""),
                answer_text=t.get("answer_text", ""),
                parsed_concepts=[
                    ExtractedConcept(**c) for c in t.get("parsed_concepts", [])
                ],
                confidence=t.get("confidence", 0.0),
                depth=t.get("depth", 1),
            )
            for t in telemetry_data
        ]
    else:
        # 模拟回放：用 golden set 前 5 条
        golden_set = load_golden_set(golden_set_path)
        telemetries = []
        for qa in golden_set[:5]:
            telemetries.append(QAStepTelemetry(
                qa_id=qa.qa_id,
                session_id="demo_session",
                model="demo-model",
                prompt_hash="hash_001",
                raw_output="",
                answer_text=qa.reference_answer,
                parsed_concepts=[
                    ExtractedConcept(canonical_name=gc.canonical_name, aliases=gc.aliases)
                    for gc in qa.golden_concepts[:5]  # 只取前5个概念模拟部分抽取
                ],
                confidence=0.85,
                depth=qa.depth,
            ))

    results = comparator.replay_batch(telemetries)
    print(f"\n=== qa_id 回放比对 ===")
    for r in results:
        if r.matched and r.extraction_result:
            er = r.extraction_result
            print(f"  {r.qa_id}: P={er.precision:.4f} R={er.recall:.4f} F1={er.f1:.4f} | "
                  f"幻觉={len(r.hallucinations)} 遗漏={len(r.omissions)}")
        else:
            print(f"  {r.qa_id}: 未匹配到 golden set")

    return results


def main():
    parser = argparse.ArgumentParser(description="「伴你成长」学习 Agent 评测管线")
    parser.add_argument("--mode", required=True,
                        choices=["extraction", "normalization", "drilldown",
                                 "hierarchy", "nonfunctional", "full", "replay"],
                        help="评测模式")
    parser.add_argument("--golden-set", default="golden_set/", help="golden set 目录")
    parser.add_argument("--clusters", help="golden concept clusters 文件")
    parser.add_argument("--events", help="下钻行为事件文件")
    parser.add_argument("--metrics", help="非功能指标文件")
    parser.add_argument("--telemetry", help="QAStep 埋点文件")
    parser.add_argument("--output", default=None, help="报告输出路径")

    args = parser.parse_args()

    if args.mode == "extraction":
        result = run_extraction_eval(args.golden_set, args.output)
    elif args.mode == "hierarchy":
        result = run_hierarchy_eval(args.golden_set)
    elif args.mode == "nonfunctional":
        result = run_nonfunctional_eval(args.metrics)
    elif args.mode == "replay":
        result = run_replay(args.golden_set, args.telemetry)
    elif args.mode == "full":
        ext = run_extraction_eval(args.golden_set)
        hie = run_hierarchy_eval(args.golden_set)
        nf = run_nonfunctional_eval()
        report = generate_report(extraction=ext, hierarchy=hie, nonfunctional=nf)
        md = report.to_markdown()
        if args.output:
            with open(args.output, "w", encoding="utf-8") as f:
                f.write(md)
            print(f"\n报告已保存: {args.output}")
        else:
            print(md)
    elif args.mode == "normalization":
        print("归一化评测需要 golden concept clusters（数据产品经理待提供）")
        print("管线框架已就绪，clusters 到位后接入跑通")
    elif args.mode == "drilldown":
        print("下钻有效性评测需要 LLM judge API key 和行为日志")
        print("配置 OPENAI_API_KEY 环境变量后运行")


if __name__ == "__main__":
    main()
