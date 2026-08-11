"""
LLM-as-judge 实现
==================

对齐 PRD 7.3:
  5 分制 rubric: 概念相关性 / 解释准确性 / 深度适配性 / 上下文连贯性
  每周人工抽检 10% 校准 judge 模型

使用 OpenAI SDK 调用 judge 模型。
支持配置 judge 模型、温度、prompt 模板。
人工校准通过 compare_with_human 方法实现。
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from typing import Optional

from .drilldown_eval import JudgeRubricScores


JUDGE_SYSTEM_PROMPT = """你是一个严格的学习内容质量评审专家。你需要按照 5 分制 rubric 对下钻回答进行评分。

评分维度（每项 1-5 分）:
1. 概念相关性 (concept_relevance): 回答是否准确切中用户点击下钻的概念，不跑题
2. 解释准确性 (explanation_accuracy): 技术内容是否正确无误，无事实性错误
3. 深度适配性 (depth_adaptability): 回答深度是否适配当前探索层级，既不过浅也不过深
4. 上下文连贯性 (context_coherence): 回答是否与上层概念链逻辑连贯，承上启下

评分标准:
- 5 分: 优秀，完全满足要求
- 4 分: 良好，基本满足要求，有小瑕疵
- 3 分: 合格，勉强达标
- 2 分: 较差，有明显问题
- 1 分: 极差，完全不相关或错误

你必须以 JSON 格式输出评分结果，格式如下:
{
  "concept_relevance": <1-5>,
  "explanation_accuracy": <1-5>,
  "depth_adaptability": <1-5>,
  "context_coherence": <1-5>,
  "reasoning": "<简要评分理由>"
}

只输出 JSON，不要输出其他内容。"""

JUDGE_USER_PROMPT_TEMPLATE = """## 探索上下文
- 当前深度: 第 {depth} 层
- 上层概念链: {parent_chain}
- 用户点击下钻的概念: {drilled_concept}

## 下钻回答内容
{answer}

## 参考概念标注
{concepts}

请按照 rubric 评分。"""


@dataclass
class JudgeConfig:
    """LLM-as-judge 配置"""
    model: str = "gpt-4o"
    temperature: float = 0.0
    max_tokens: int = 500
    api_key_env: str = "OPENAI_API_KEY"
    base_url: Optional[str] = None
    # 人工校准
    calibration_sample_ratio: float = 0.10  # 每周抽检 10%


class LLMJudge:
    """LLM-as-judge 评测器"""

    def __init__(self, config: Optional[JudgeConfig] = None):
        self.config = config or JudgeConfig()
        self._client = None

    @property
    def client(self):
        """懒加载 OpenAI client"""
        if self._client is None:
            try:
                from openai import OpenAI
                api_key = os.environ.get(self.config.api_key_env, "")
                kwargs = {"api_key": api_key}
                if self.config.base_url:
                    kwargs["base_url"] = self.config.base_url
                self._client = OpenAI(**kwargs)
            except ImportError:
                raise RuntimeError(
                    "openai package not installed. Run: pip install openai"
                )
        return self._client

    def judge(
        self,
        depth: int,
        parent_chain: list[str],
        drilled_concept: str,
        answer: str,
        concepts: list[str],
    ) -> JudgeRubricScores:
        """
        对一条下钻回答进行 LLM-as-judge 评分。

        Args:
            depth: 当前探索深度
            parent_chain: 上层概念链
            drilled_concept: 用户点击下钻的概念
            answer: 下钻回答内容
            concepts: 回答中标注的概念列表

        Returns:
            JudgeRubricScores: 5 维度评分 + 加权总分
        """
        user_prompt = JUDGE_USER_PROMPT_TEMPLATE.format(
            depth=depth,
            parent_chain=" -> ".join(parent_chain) if parent_chain else "(根问题)",
            drilled_concept=drilled_concept,
            answer=answer,
            concepts=", ".join(concepts) if concepts else "(无概念标注)",
        )

        try:
            response = self.client.chat.completions.create(
                model=self.config.model,
                temperature=self.config.temperature,
                max_tokens=self.config.max_tokens,
                messages=[
                    {"role": "system", "content": JUDGE_SYSTEM_PROMPT},
                    {"role": "user", "content": user_prompt},
                ],
            )
            content = response.choices[0].message.content.strip()
            # 尝试解析 JSON
            if content.startswith("```json"):
                content = content[7:]
            if content.endswith("```"):
                content = content[:-3]
            scores_data = json.loads(content)

            scores = JudgeRubricScores(
                concept_relevance=float(scores_data.get("concept_relevance", 0)),
                explanation_accuracy=float(scores_data.get("explanation_accuracy", 0)),
                depth_adaptability=float(scores_data.get("depth_adaptability", 0)),
                context_coherence=float(scores_data.get("context_coherence", 0)),
                judge_reasoning=scores_data.get("reasoning", ""),
            )
            # 计算加权总分
            from .drilldown_eval import DrilldownEvaluator
            scores.overall = DrilldownEvaluator.compute_overall_score(scores)
            return scores

        except Exception as e:
            # 返回默认分数，标记异常
            return JudgeRubricScores(
                judge_reasoning=f"Judge error: {e}",
            )

    def compare_with_human(
        self,
        judge_scores: list[JudgeRubricScores],
        human_scores: list[JudgeRubricScores],
    ) -> dict:
        """
        人工校准: 比较 judge 评分与人工评分的一致率。

        对齐 PRD 7.3: 每周人工抽检 10% 校准 judge 模型。

        Args:
            judge_scores: judge 模型评分列表
            human_scores: 对应的人工评分列表

        Returns:
            一致率、平均偏差、各维度相关系数
        """
        if len(judge_scores) != len(human_scores):
            raise ValueError("judge_scores and human_scores must have same length")

        n = len(judge_scores)
        if n == 0:
            return {"agreement_rate": 0.0, "avg_deviation": 0.0}

        dimensions = ["concept_relevance", "explanation_accuracy",
                       "depth_adaptability", "context_coherence"]

        # 完全一致率（所有维度都相同）
        exact_agreements = 0
        total_deviations = []

        for js, hs in zip(judge_scores, human_scores):
            all_same = True
            for dim in dimensions:
                j_val = getattr(js, dim)
                h_val = getattr(hs, dim)
                if abs(j_val - h_val) > 0.01:
                    all_same = False
                total_deviations.append(abs(j_val - h_val))
            if all_same:
                exact_agreements += 1

        # ±1 分内一致率
        within_one = sum(1 for d in total_deviations if d <= 1.0) / len(total_deviations)

        return {
            "sample_count": n,
            "exact_agreement_rate": exact_agreements / n,
            "within_one_point_rate": within_one,
            "avg_deviation": sum(total_deviations) / len(total_deviations),
        }
