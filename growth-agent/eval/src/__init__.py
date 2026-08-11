"""
「伴你成长」学习 Agent — 评测管线框架
=======================================
概念抽取质量 / 概念归一化准确率 / 下钻有效性 / 层级累积质量 / 非功能指标门禁

模块清单:
  models           — 数据模型 (ConceptNode / QAStep / GoldenQA 对齐架构文档)
  matcher          — canonical_name + aliases 双轨匹配引擎
  extraction_eval  — 概念抽取质量 (实体级 P/R/F1 + 错误分类)
  normalization_eval — 概念归一化准确率 (合并 P/R + 误合并率)
  drilldown_eval   — 下钻有效性 (LLM-as-judge rubric + 行为信号)
  hierarchy_eval   — 层级累积质量 (第 3-5 层深度采样)
  nonfunctional_eval — 非功能指标门禁
  llm_judge        — LLM-as-judge 实现
  report_generator — 评测报告生成
  replay           — qa_id 回放比对 golden set
  data_contracts   — 数据接口契约 (QAStep 埋点 / 非功能可观测接口)
"""

__version__ = "1.0.0"
