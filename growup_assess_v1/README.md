# 「伴你成长」学习 Agent — 评测管线框架

> **版本**: v1.0  
> **日期**: 2026-08-05  
> **Phase 1 交付**: 3 个试点领域 Golden Set + 完整评测管线 + 数据接口契约 + 报告模板

## 目录结构

```
artifacts/
├── golden_set/                  # Golden Test Set (3 领域 × 20 QA)
│   ├── schema.json              #   数据 schema 定义
│   ├── machine_learning.json    #   机器学习 20 QA (含 depth 1/3/4/5)
│   ├── computer_networks.json   #   计算机网络 20 QA
│   └── database_systems.json    #   数据库系统 20 QA
├── src/                         # 评测管线框架 (Python)
│   ├── models.py                #   数据模型 (ConceptNode/QAStep 对齐架构文档)
│   ├── matcher.py               #   canonical_name + aliases 双轨匹配引擎
│   ├── extraction_eval.py       #   概念抽取质量 (P/R/F1 + 错误分类)
│   ├── normalization_eval.py    #   概念归一化准确率 (合并P/R + 误合并率)
│   ├── drilldown_eval.py        #   下钻有效性 (LLM-as-judge + 行为信号)
│   ├── hierarchy_eval.py        #   层级累积质量 (depth 3-5 采样)
│   ├── nonfunctional_eval.py    #   非功能指标门禁
│   ├── llm_judge.py             #   LLM-as-judge 实现
│   ├── report_generator.py      #   评测报告生成 (Markdown/JSON)
│   ├── replay.py                #   qa_id 回放比对 golden set
│   └── data_contracts.py        #   数据接口契约 (3份)
├── scripts/
│   └── run_eval.py              # 评测主入口脚本
├── generate_golden_set.py       # Golden Set 生成脚本
├── demo_report.md               # 示例评测报告
└── README.md                    # 本文档
```

## 快速开始

```bash
# 1. 概念抽取评测
python scripts/run_eval.py --mode extraction --golden-set golden_set/

# 2. 层级累积质量评测
python scripts/run_eval.py --mode hierarchy --golden-set golden_set/

# 3. 非功能指标门禁
python scripts/run_eval.py --mode nonfunctional

# 4. qa_id 回放比对
python scripts/run_eval.py --mode replay --golden-set golden_set/

# 5. 全量评测报告
python scripts/run_eval.py --mode full --golden-set golden_set/ --output report.md
```

## Golden Set 说明

### 3 个试点领域
| 领域 | QA 数 | Golden 概念数 | 深度分布 |
|------|-------|-------------|---------|
| 机器学习 | 20 | 151 | d1×14 + d3×2 + d4×2 + d5×2 |
| 计算机网络 | 20 | 158 | d1×14 + d3×2 + d4×2 + d5×2 |
| 数据库系统 | 20 | 167 | d1×14 + d3×2 + d4×2 + d5×2 |
| **合计** | **60** | **476** | d1×42 + d3×6 + d4×6 + d5×6 |

### 匹配规则 (canonical_name + aliases 双轨)
1. 规范名精确匹配: `pred.canonical_name == golden.canonical_name`
2. 预测规范名命中 golden 别名: `pred.canonical_name ∈ golden.aliases`
3. 预测别名命中 golden 规范名: `∃ a ∈ pred.aliases, a == golden.canonical_name`
4. 别名交集: `pred.aliases ∩ golden.aliases ≠ ∅`

### 错误分类
- **精确命中 (TP)**: 预测概念匹配到 golden 概念
- **幻觉 (FP)**: 预测概念未匹配到任何 golden 概念
- **遗漏 (FN)**: golden 概念未被任何预测概念匹配

## 评测维度与门禁

| 维度 | 指标 | 门禁 | 状态 |
|------|------|------|------|
| 概念抽取质量 | 实体级 F1 | ≥ 0.80 | ✅ 管线就绪 |
| 概念归一化 | 误合并率 | < 5% | ⏳ 待 clusters |
| 概念归一化 | 合并 Recall | > 85% | ⏳ 待 clusters |
| 下钻有效性 | judge 均分 | ≥ 3.5 | ✅ 管线就绪 |
| 下钻有效性 | 立即回退率 | < 15% | ✅ 管线就绪 |
| 层级累积质量 | F1 衰减率 | ≤ 20% | ✅ 管线就绪 |
| 流式中断恢复 | 成功率 | > 95% | ✅ 管线就绪 |
| 异步补标注 | 完成率 | > 90% | ✅ 管线就绪 |
| Dead Letter | 比例 | < 2% | ✅ 管线就绪 |
| 回填延迟 | P95 | < 30s | ✅ 管线就绪 |
| 熔断告警 | 触发率 | > 10%/h | ✅ 管线就绪 |

## 依赖状态

| 依赖 | 提供方 | 接收方 | 状态 | 接口契约 |
|------|--------|--------|------|---------|
| QAStep 埋点数据 | Agent 研发 | AI 评测 | ✅ 已接受 | `src/data_contracts.py` §1 |
| 非功能指标接口 | Harness | AI 评测 | ✅ 已纳入 | `src/data_contracts.py` §2 |
| Golden concept clusters | 数据产品经理 | AI 评测 | ⏳ 待提供 | `src/data_contracts.py` §3 |

## 扩展到 5×40

当前 Phase 1 交付 3 领域 × 20 QA。扩展到 5×40 的步骤：
1. 新增 2 个领域（建议：操作系统、分布式系统）
2. 每领域扩充到 40 QA（depth 1×28 + d3×4 + d4×4 + d5×4）
3. 运行 `generate_golden_set.py` 生成 JSON
4. 评测管线无需修改，自动适配新数据
