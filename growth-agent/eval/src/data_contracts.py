"""
数据接口契约
============

定义评测管线与外部系统之间的数据接口契约:
  1. QAStep 埋点数据契约 (Agent 研发工程师提供)
  2. 非功能指标可观测接口契约 (Harness 工程师提供)
  3. Golden concept clusters 数据契约 (数据产品经理提供)

对齐技术架构文档 十 跨模块依赖对齐状态:
  - QAStep 埋点: Agent 研发 -> AI 评测, 已接受落地
  - 非功能指标可观测接口: AI 评测 -> Harness, 已纳入
  - golden concept clusters: AI 评测 -> 数据产品经理, 待提供
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

from .models import QAStepTelemetry, NormalizationDecision, NonFunctionalMetrics


# ── 1. QAStep 埋点数据契约 ─────────────────────────────

QASTEP_TELEMETRY_CONTRACT = """
QAStep 埋点数据契约 (Agent 研发 -> AI 评测)
============================================

每次概念抽取调用必须记录以下字段，全部只追加写入 (append-only):

字段说明:
  qa_id          : str    - QAStep 唯一标识，用于回放比对 golden set
  session_id     : str    - 所属会话 ID
  model          : str    - 使用的模型名 (如 "gpt-4o", "doubao-pro")
  prompt_hash    : str    - prompt 模板哈希，用于追溯 prompt 版本
  raw_output     : str    - 模型原始输出 (含 sentinel 分割的正文 + JSON)
  answer_text    : str    - 解析后的正文 (sentinel 之前的部分)
  parsed_concepts: list   - 解析出的概念列表，每项含:
                            {
                              "canonical_name": str,
                              "aliases": [str],
                              "confidence": float,
                              "raw_text": str       // 原始抽取文本
                            }
  confidence     : float  - 整体抽取置信度 (0-1)
  depth          : int    - 探索树深度 (1=根问题)
  parent_qa_id   : str?   - 父 QAStep ID (下钻时挂载)
  parent_concept_chain: [str] - 父概念链 (从根到当前层的概念名序列)
  timestamp      : str    - ISO 8601 时间戳

归一化决策日志 (同通道追加写入):
  qa_id            : str   - 关联的 QAStep
  candidate_name   : str   - 待归一化的概念名
  matched_alias    : str   - 匹配到的别名 (精确匹配时)
  similarity_score : float - embedding 相似度
  action           : str   - "merge" | "keep"
  llm_verdict      : str   - LLM 判定理由 (灰区判定时)
  stage            : str   - "alias_exact" | "embedding" | "llm_gray"
  timestamp        : str   - ISO 8601

输出格式: NDJSON (每行一条 JSON 记录)
存储路径: /data/telemetry/qa_steps/{date}/{qa_id}.jsonl
回放方式: 按 qa_id 检索 -> 解析 parsed_concepts -> 与 golden set 比对
"""

# ── 2. 非功能指标可观测接口契约 ──────────────────────

NONFUNCTIONAL_OBSERVABILITY_CONTRACT = """
非功能指标可观测接口契约 (AI 评测 -> Harness)
==============================================

Harness 工程师需暴露以下指标供评测管线采集:

1. 流式中断恢复指标
   - 接口: GET /metrics/streaming-recovery?start={ts}&end={ts}
   - 返回: {
       "total_interruptions": int,     // 总中断次数
       "successful_recoveries": int,    // 成功恢复次数
       "success_rate": float,           // 恢复成功率
       "by_type": {                     // 按中断类型分组
         "user_back": {...},
         "network_disconnect": {...}
       }
     }

2. 异步补标注指标
   - 接口: GET /metrics/async-backfill?start={ts}&end={ts}
   - 返回: {
       "total_tasks": int,             // 总补标注任务数
       "completed_tasks": int,          // 成功完成数
       "dead_letter_tasks": int,        // dead letter 数
       "completion_rate": float,        // 完成率
       "dead_letter_rate": float,       // dead letter 比例
       "p95_latency_ms": float,         // P95 回填延迟
       "p99_latency_ms": float          // P99 回填延迟
     }

3. 熔断指标
   - 接口: GET /metrics/circuit-breaker?start={ts}&end={ts}
   - 返回: {
       "total_triggers": int,           // 熔断触发总次数
       "trigger_rate_per_hour": float,  // 每小时触发率
       "by_endpoint": {                 // 按模型端点分组
         "endpoint_a": {"triggers": int, "error_rate": float},
         ...
       }
     }

聚合接口 (一次性获取所有非功能指标):
   - 接口: GET /metrics/nonfunctional-snapshot?window=1h
   - 返回: NonFunctionalMetrics 结构

采样频率: 每 5 分钟采集一次，每小时聚合一次
告警通道: 熔断触发率 > 10%/h 时推送飞书告警
"""

# ── 3. Golden Concept Clusters 数据契约 ──────────────

GOLDEN_CLUSTERS_CONTRACT = """
Golden Concept Clusters 数据契约 (数据产品经理 -> AI 评测)
=========================================================

用于概念归一化准确率评测，需提供 100+ 组已知应合并 / 不应合并的概念对。

数据格式: JSON 数组，每项格式如下:
  {
    "pair_id": "cluster_001",
    "concept_a": "梯度下降",              // canonical_name 或别名
    "concept_b": "gradient descent",      // canonical_name 或别名
    "should_merge": true,                 // true=应合并, false=不应合并
    "difficulty": "easy",                 // easy / normal / hard
    "note": "中英文同义，别名表应覆盖"     // 标注备注
  }

难度定义:
  - easy:   明确应/不应合并，别名表或简单 embedding 即可判定
  - normal: 需要一定语义理解，可能进入 LLM 灰区判定
  - hard:   高度相似但语义不同（如 "模型蒸馏" vs "知识蒸馏"），或
            需要领域知识才能区分的边界 case

分布要求:
  - 应合并对: ~60% (覆盖中英文、缩写、同义词、近义词)
  - 不应合并对: ~40% (覆盖形似义异、跨领域同名、部分匹配)
  - 难度分布: easy 30%, normal 50%, hard 20%

当前状态: 待提供 (不阻塞管线框架搭建，到位后接入跑通)
"""


def print_contracts():
    """打印所有数据接口契约"""
    print("=" * 70)
    print(QASTEP_TELEMETRY_CONTRACT)
    print("=" * 70)
    print(NONFUNCTIONAL_OBSERVABILITY_CONTRACT)
    print("=" * 70)
    print(GOLDEN_CLUSTERS_CONTRACT)
