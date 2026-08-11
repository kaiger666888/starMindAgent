"""
canonical_name + aliases 双轨匹配引擎
=====================================

匹配规则（对齐 PRD §7.1）:
  预测概念 P 与 golden 概念 G 匹配，当且仅当以下任一成立:
    1. P.canonical_name == G.canonical_name          （规范名精确匹配）
    2. P.canonical_name ∈ G.aliases                    （预测规范名命中 golden 别名）
    3. ∃ a ∈ P.aliases, a == G.canonical_name          （预测别名命中 golden 规范名）
    4. ∃ a ∈ P.aliases, a ∈ G.aliases                   （别名交集）

所有匹配在大小写归一化 + 去空白后进行。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

from .models import ConceptNode, ExtractedConcept, GoldenConcept


def _normalize(name: str) -> str:
    """名称归一化：小写 + 去首尾空白"""
    return name.lower().strip()


@dataclass
class MatchResult:
    """单次匹配结果"""
    matched: bool
    match_type: str = ""          # canonical / alias_pred / alias_golden / alias_intersect
    matched_golden_idx: int = -1  # 匹配到的 golden 概念索引
    detail: str = ""


@dataclass
class ExtractionComparison:
    """一次抽取评测的完整比对结果"""
    # 匹配关系: predicted_idx -> matched_golden_idx (-1 = 未匹配)
    pred_to_golden: dict[int, int] = field(default_factory=dict)
    # 已匹配的 golden 概念索引集合
    matched_golden_indices: set[int] = field(default_factory=set)
    # 逐条匹配详情
    match_details: list[MatchResult] = field(default_factory=list)


class DualTrackMatcher:
    """canonical_name + aliases 双轨匹配器"""

    @staticmethod
    def match_single(
        pred: ExtractedConcept | ConceptNode | GoldenConcept,
        golden: ExtractedConcept | ConceptNode | GoldenConcept,
    ) -> MatchResult:
        """
        判断单个预测概念是否匹配单个 golden 概念。

        支持传入 ExtractedConcept / ConceptNode / GoldenConcept，
        它们都有 canonical_name 和 aliases 字段。
        """
        pred_canonical = _normalize(pred.canonical_name)
        golden_canonical = _normalize(golden.canonical_name)
        pred_aliases = {_normalize(a) for a in pred.aliases}
        golden_aliases = {_normalize(a) for a in golden.aliases}

        # 规则 1: 规范名精确匹配
        if pred_canonical and pred_canonical == golden_canonical:
            return MatchResult(
                matched=True,
                match_type="canonical",
                detail=f"canonical match: '{pred_canonical}'",
            )

        # 规则 2: 预测规范名命中 golden 别名
        if pred_canonical and pred_canonical in golden_aliases:
            return MatchResult(
                matched=True,
                match_type="alias_golden",
                detail=f"pred canonical '{pred_canonical}' matches golden alias",
            )

        # 规则 3: 预测别名命中 golden 规范名
        if golden_canonical in pred_aliases:
            return MatchResult(
                matched=True,
                match_type="alias_pred",
                detail=f"pred alias matches golden canonical '{golden_canonical}'",
            )

        # 规则 4: 别名交集
        alias_intersect = pred_aliases & golden_aliases
        if alias_intersect:
            return MatchResult(
                matched=True,
                match_type="alias_intersect",
                detail=f"alias intersection: {alias_intersect}",
            )

        return MatchResult(matched=False, detail="no match")

    @staticmethod
    def compare(
        predicted: list[ExtractedConcept],
        golden: list[GoldenConcept],
    ) -> ExtractionComparison:
        """
        批量比对预测概念列表与 golden 概念列表。

        匹配策略：贪心一对一匹配（每个 golden 概念最多被一个预测概念匹配）。
        优先匹配 canonical 类型，再匹配 alias 类型，减少误配。

        返回 ExtractionComparison，包含完整匹配关系。
        """
        comparison = ExtractionComparison()

        # 第一轮：canonical 匹配（最高优先级）
        used_golden: set[int] = set()
        for i, pred in enumerate(predicted):
            comparison.pred_to_golden.setdefault(i, -1)
            for j, gold in enumerate(golden):
                if j in used_golden:
                    continue
                result = DualTrackMatcher.match_single(pred, gold)
                if result.matched and result.match_type == "canonical":
                    comparison.pred_to_golden[i] = j
                    comparison.matched_golden_indices.add(j)
                    used_golden.add(j)
                    comparison.match_details.append(result)
                    break

        # 第二轮：alias 匹配（对第一轮未匹配的预测概念）
        for i, pred in enumerate(predicted):
            if comparison.pred_to_golden.get(i, -1) != -1:
                continue  # 已在第一轮匹配
            for j, gold in enumerate(golden):
                if j in used_golden:
                    continue
                result = DualTrackMatcher.match_single(pred, gold)
                if result.matched:
                    result.matched_golden_idx = j
                    comparison.pred_to_golden[i] = j
                    comparison.matched_golden_indices.add(j)
                    used_golden.add(j)
                    comparison.match_details.append(result)
                    break
            else:
                # 未匹配到任何 golden 概念 → 幻觉
                comparison.match_details.append(
                    MatchResult(matched=False, detail=f"hallucination: '{pred.canonical_name}'")
                )

        # 标记遗漏的 golden 概念
        for j in range(len(golden)):
            if j not in comparison.matched_golden_indices:
                comparison.match_details.append(
                    MatchResult(
                        matched=False,
                        match_type="omission",
                        matched_golden_idx=j,
                        detail=f"omission: golden concept '{golden[j].canonical_name}' not extracted",
                    )
                )

        return comparison
