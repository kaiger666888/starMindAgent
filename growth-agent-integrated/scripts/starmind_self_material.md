# starMindAgent 后端架构学习材料

## 项目定位

starMindAgent（「伴你成长」）是一个概念探索式学习工具。用户提一个问题，LLM 流式回答，回答过程中自动抽取关键概念（Concept），用户点击概念下钻开新探索层，形成一棵探索树；同时右侧概念图展示概念间联系与热度，档案视图汇总学习画像、概念诊断与学习足迹。产品隐喻是「学习手账/成长日志」，不是工具型 SaaS。

核心交互闭环：提问 → 流式回答 + 概念高亮 → 点概念下钻（新层）→ 层层深入形成探索树 → 概念图沉淀跨会话的知识结构 → 学习画像反馈推荐。

## 整体架构

后端是 Python/FastAPI 单体，分层清晰：

- **api 层**（app/api/）：routes_qa（QAStep 状态机三个出口 + SSE 流式回推）、routes_concept（概念服务：merge/undo/graph/explore）、routes_learning（学习材料导入）、routes_memory（学习记忆：会话列表/画像/导出）、routes_harness（可观测性指标）。
- **qastep 层**（app/qastep/）：QAStepPipeline 状态机（后端核心，P0）、repository（DB 访问）、state_machine 的 SSE 事件流。
- **inference 层**（app/inference/）：backend（LLM 后端抽象与实现）、client（InferenceClient）、context（prompt 组装）、constraints（约束解码）、sentinel（协议检测）。
- **harness 层**（app/harness/）：InferenceSession 封装、熔断器 circuit_breaker、checkpoint store、补标注 worker（reannotation）、恢复 recovery、可观测性 observability。
- **concept 层**（app/concept/）：normalization（归一化三级流水线）、service（概念服务）、audit（合并审计与 undo 回放）、thresholds（灰度阈值）。
- **learning 层**（app/learning/）：service（markdown 导入建 L0 根 + 材料检索）、keyword_candidates。
- **memory 层**（app/memory/）：service（会话/画像）、export（结构化导出）。
- **search 层**（app/search/）：provider（SearXNG 联网搜索）、codebase（ripgrep 代码库 grounding）。

LLM 调用走异步任务队列 + SSE 流式回推。数据库 PostgreSQL（asyncpg），支持 SQLite 降级。

## 核心机制一：QAStep 状态机

QAStep 是「一次问答」的实体，每层探索树节点就是一个 QAStep。状态流转：generating（推理中）→ extracting（概念抽取中）→ waiting（等待用户操作）。三个出口：

1. **下钻（出口1）**：点概念 → fork 子 QAStep 挂 parent_qa_id，depth+1，超过 6 层触发膨胀降级（DepthLimitReached，标注已有概念不新建推理）。
2. **回退（出口2）**：rollback 回上层，栈式恢复，当前层标记 browsed_not_drilled；若涉及概念 explore_count≥2 且未下钻则标记 understood（前端入口变灰）。
3. **新问题（出口3）**：开新探索树，parent_qa_id=None。

QAStepPipeline.run() 是异步生成器，yield 的 SSE 事件：status（状态切换）、answer_delta（流式正文增量）、concept_candidates（流式动态概念候选，见机制三）、search_sources（联网搜索来源）、concepts（权威概念列表）、done（完成）。

## 核心机制二：sentinel 协议（正文与概念块并存）

单次 LLM 调用的输出协议：模型先输出自然语言正文，然后另起一行输出 sentinel 标记（CONCEPT_BLOCK，集中配置在 config.py 的 settings.concept_sentinel），最后输出符合 ConceptBlock schema 的 JSON。这样一次调用同时产出回答和概念抽取。

降级链 L0-L3：L0 一次成功（正文+合法 JSON）；L1 = sentinel JSON 损坏，重试一次；L2 = 模型不支持并存，拆两次调用（先 stream 正文再 extract_only 抽概念）；L3 = 本地启发式兜底。熔断按端点维度统计。

概念归一化（ConceptNormalization）在概念入图前跑三级流水线：第一级别名精确匹配（alias_exact_match）直接归并；第二级 embedding 召回，相似度高于高阈值归并、低于低阈值新建独立节点；第三级灰区交给 LLMJudge 二次判定。所有合并写 audit log 支持反向回放撤销（undo_merge）。

## 核心机制三：流式动态概念候选

概念 chip 不等整层完成（thinking 模式下要 30-60s），三层来源权威优先：

1. 种子词：前端从 question 抽「X」引号词和英文术语，流式第 0 秒参与高亮。
2. 增量候选：后端 StreamCandidateExtractor 挂在 answer_delta 流上（~80 字句界切分、jieba TF-IDF + 名词 bigram、功能词黑名单），SSE concept_candidates 事件推前端。纯本地 CPU 计算（0.8ms/层），不碰 LLM。
3. 权威列表：整层生成完的归一化概念（concepts 事件），到达后与候选按 canonical_name + aliases 合并去重。

## 核心机制四：两轮 agentic 工具调用

OpenAICompatibleBackend.stream() 支持两个工具，模型自判 + 确定性预检索双层触发：

- **web_search**：SearXNG 本地容器（127.0.0.1:8888，brave+google cse 聚合）。模型对时效性问题自动调用，每问最多 1 轮 3 个 query。搜索结果拼进 user prompt（不走 role=tool 回填——实测网关对 tool 结果消息续写不稳定，约 50% 空响应）。
- **code_query**：ripgrep 代码库检索（CODEBASE_DIR 环境变量指定仓库）。确定性预检索：prompt 尾部含驼峰/下划线标识符特征词时直接 rg 注入（不赌模型自判）；同时保留 tools 轮让模型主动深查。

两轮结构：第一轮流式 + tools 定义（乐观直吐正文，直答场景零开销）；触发工具时执行检索把结果拼进 user prompt 进第二轮最终生成。

## 核心机制五：学习材料导入与检索

导入 markdown（POST /learning/import）：存 LearningMaterial + 建 L0 根 QAStep（depth=0, status=waiting, answer=全文）+ 三段式概念抽取（jieba 候选生成 → LLM 精判 → 降级标题抽取）。

提问带 material_id 时，get_material_context 按问题检索材料相关段落注入 prompt（检索零命中返回空不注入，防止退化成材料开头）。

## 关键模块说明

- **main.py**：FastAPI 入口，lifespan 里启动 harness 补标注 worker 和 preset concepts seed。
- **qastep/state_machine.py**：QAStepPipeline，概念链注入（沿 parent_qa_id 回溯构建 ChainNode 压缩进 prompt，防回答同质化）、层摘要后台异步化（不阻塞 done）。
- **inference/backend.py**：LLMBackend Protocol + OpenAICompatibleBackend（higress 网关，thinking disabled 后 TTFT ~2s）+ AnthropicBackend + StubLLMBackend（本地离线）。httpx 模块级共享连接池。
- **inference/context.py**：build_prompt 组装（SYSTEM_PROMPT + 概念链 + 材料段落 + question），总 token 预算控制。
- **harness/app.py**：get_harness 单例，session_for 为每个 QAStep 创建 InferenceSession。
- **concept/service.py**：概念图（session 级/全局聚合）、merge/undo、探索计数、领域图扩展。
- **memory/export.py**：会话导出 md 学习手账（层级映射标题深度）/ json 备份，导出 md 可再导入开新探索（闭环）。

## 数据模型

- **qa_session**：学习会话（user_id + domain_tag）。
- **qa_step**：问答步骤（question/answer/status/depth/parent_qa_id/material_id），树结构靠 parent_qa_id 自连接。
- **concept_node**：概念节点（canonical_name/aliases/explore_count），跨会话共享。
- **concept_edge**：概念关系边（session_id + source_id/target_id/origin）。origin 三种：user_click（用户下钻点击）、co_occurrence（同次抽取共现）、domain_graph（领域图扩展）。
- **learning_material**：导入的学习材料（content markdown + content_plain 纯文本）。
- **audit_log**：合并审计（merge/keep 判定与依据），支持 undo 回放。
- **app_user / user_profile**：用户与学习画像（mastered/weak/interests/recommendation）。

## 关键设计取舍

1. **prompt 注入优于 role=tool 回填**：网关对 tool 结果消息续写不稳定，搜索/代码结果都拼进 user prompt（与 material_context 同构）。
2. **概念抽取与正文并存（sentinel 协议）优于拆两次调用**：L0 一次成功率高（thinking disabled 后），拆分是 L2 降级路径而非默认。
3. **层摘要/埋点后台化**：都是完整 LLM/DB 往返，串行会拖慢 done 一倍，create_task 后台执行落库。
4. **归一化并行化**：5 概念串行 1.8s → gather 并发 0.5s（每个 2-3 次 DB 往返）。
5. **膨胀控制**：下钻超 6 层降级、概念探索计数防重复、回答限 500 字（长输出会挤掉 sentinel JSON 且推高网关内存峰值，曾致 1210 OOM）。
6. **搜索/代码 grounding 开关化**：SEARCH_ENABLED 和 CODEBASE_DIR 环境变量控制，未配置零影响。
