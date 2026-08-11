"""
qa_id 回放比对 golden set
=========================

对齐技术架构文档 3.3:
  评测可直接按 qa_id 回放与 golden set 比对。

支持两种回放模式:
  1. 从 QAStep 埋点数据回放（已落盘的 raw_output + parsed_concepts）
  2. 从归一化 audit log 回放（merge/keep 决策链路）

用法:
  from src.replay import ReplayComparator
  comparator = ReplayComparator(golden_set_path="golden_set/")
  result = comparator.replay_qa("ml_001", telemetry)
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from typing import Optional

from .models import GoldenQA, GoldenConcept, QAStepTelemetry, ExtractedConcept
from .extraction_eval import ExtractionEvaluator, QAEvalResult
from .matcher import DualTrackMatcher


@dataclass
class ReplayResult:
    """qa_id 回放比对结果"""
    qa_id: str
    matched: bool = False                    # 是否在 golden set 中找到对应 QA
    # 抽取评测
    extraction_result: Optional[QAEvalResult] = None
    # 原始比对明细
    predicted_concepts: list[str] = field(default_factory=list)
    golden_concepts: list[str] = field(default_factory=list)
    matched_pairs: list[tuple[str, str]] = field(default_factory=list)
    hallucinations: list[str] = field(default_factory=list)
    omissions: list[str] = field(default_factory=list)
    # 埋点信息
    model: str = ""
    prompt_hash: str = ""
    confidence: float = 0.0
    depth: int = 1


class ReplayComparator:
    """qa_id 回放比对器"""

    def __init__(self, golden_set_path: str = "golden_set"):
        """
        Args:
            golden_set_path: golden set JSON 文件所在目录
        """
        self.golden_set_path = golden_set_path
        self.golden_index: dict[str, GoldenQA] = {}
        self._load_golden_set()

    def _load_golden_set(self):
        """加载 golden set 目录下所有 JSON 文件，构建 qa_id 索引"""
        if not os.path.isdir(self.golden_set_path):
            return

        for fname in os.listdir(self.golden_set_path):
            if not fname.endswith(".json") or fname == "schema.json":
                continue
            fpath = os.path.join(self.golden_set_path, fname)
            try:
                with open(fpath, "r", encoding="utf-8") as f:
                    data = json.load(f)
                # 支持 {qa_pairs: [...]} 或 [...] 两种格式
                qa_list = data.get("qa_pairs", data) if isinstance(data, dict) else data
                if not isinstance(qa_list, list):
                    continue
                for qa_data in qa_list:
                    qa = self._parse_golden_qa(qa_data)
                    if qa:
                        self.golden_index[qa.qa_id] = qa
            except (json.JSONDecodeError, KeyError):
                continue

    def _parse_golden_qa(self, data: dict) -> Optional[GoldenQA]:
        """从 JSON dict 解析 GoldenQA"""
        try:
            golden_concepts = []
            for gc in data.get("golden_concepts", []):
                golden_concepts.append(GoldenConcept(
                    canonical_name=gc["canonical_name"],
                    aliases=gc.get("aliases", []),
                    in_answer=gc.get("in_answer", True),
                    note=gc.get("note", ""),
                ))
            return GoldenQA(
                qa_id=data["qa_id"],
                domain=data.get("domain", ""),
                depth=data.get("depth", 1),
                parent_concept_chain=data.get("parent_concept_chain", []),
                question=data.get("question", ""),
                reference_answer=data.get("reference_answer", ""),
                golden_concepts=golden_concepts,
                tags=data.get("tags", []),
            )
        except (KeyError, TypeError):
            return None

    def replay_qa(
        self,
        qa_id: str,
        telemetry: QAStepTelemetry,
    ) -> ReplayResult:
        """
        按 qa_id 回放比对 golden set。

        Args:
            qa_id: 要回放的 QA ID
            telemetry: 对应的 QAStep 埋点数据

        Returns:
            ReplayResult: 包含抽取评测结果和比对明细
        """
        result = ReplayResult(qa_id=qa_id)
        result.model = telemetry.model
        result.prompt_hash = telemetry.prompt_hash
        result.confidence = telemetry.confidence
        result.depth = telemetry.depth

        golden_qa = self.golden_index.get(qa_id)
        if golden_qa is None:
            result.matched = False
            return result

        result.matched = True

        # 执行抽取评测
        evaluator = ExtractionEvaluator()
        eval_result = evaluator.evaluate_qa(golden_qa, telemetry.parsed_concepts)
        result.extraction_result = eval_result

        # 填充比对明细
        result.predicted_concepts = [c.canonical_name for c in telemetry.parsed_concepts]
        result.golden_concepts = [c.canonical_name for c in golden_qa.golden_concepts]
        result.matched_pairs = eval_result.matched_pairs
        result.hallucinations = eval_result.hallucinated_concepts
        result.omissions = eval_result.omitted_concepts

        return result

    def replay_batch(
        self,
        telemetries: list[QAStepTelemetry],
    ) -> list[ReplayResult]:
        """批量回放比对"""
        return [self.replay_qa(t.qa_id, t) for t in telemetries]

    def list_golden_qa_ids(self) -> list[str]:
        """列出所有 golden set 中的 qa_id"""
        return sorted(self.golden_index.keys())
