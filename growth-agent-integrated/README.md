# 「伴你成长」学习 Agent — 后端 + 前端参考实现

> 对应技术架构文档 v1.0。本文档是 P0/QAStep 状态机 + Concept 服务 + 数据层 + FastAPI/SSE 后端 + React/Cytoscape.js 前端的可落地参考实现。
> 所有设计严格对齐技术架构文档第三节(QAStep)、第四节(数据模型)、第五节(归一化)、第六节(推理协议)、第七节(Harness)、第八节(Concept 服务)。

## 一、模块映射（技术架构文档 → 本仓库）

| 技术文档章节 | 仓库文件 | 说明 |
|-|-|-|
| §3 QAStep 状态机 | `backend/app/qastep/state_machine.py` | generating→extracting→waiting + 三出口(fork/rollback/new tree) + 乐观锁 + 埋点 |
| §3.2 并发一致性 | `backend/app/qastep/repository.py` | 乐观锁 `version` 校验 + 前端在途互斥(store) |
| §3.3 QAStep 埋点 | `repository.persist_telemetry` + `models.AuditLog` | {qa_id,model,prompt_hash,raw_output,parsed_concepts,aliases,confidence} |
| §4 数据模型 | `backend/app/models/tables.py` + `migrations/001_init.sql` | ConceptNode/Edge/QASession/QAStep/AuditLog/BackfillTask |
| §4.2 三状态视图 | `migrations` 的 `v_graph_*` view + `concept/service.get_graph` | 单表 + origin 派生 |
| §4.4 探索热度 | `concept/service.increment_explore` + `_color_tier` | 0灰/1绿/≥2对数四档红 |
| §5 归一化流水线 | `backend/app/concept/normalization.py` | 别名精确→embedding召回→LLM灰区 |
| §5 阈值独立服务 | `backend/app/concept/thresholds.py` | 热加载 JSON，灰度调参不动主链路 |
| §6 推理输出协议 | `backend/app/inference/protocol.py` | Sentinel 分割 + StreamSplitter + JSON 累积 + L0-L3 |
| §7 InferenceSession | `inference/protocol.py` 的 `InferenceSession` Protocol | Harness 工程师提供实现，QAStep 只消费语义事件 |
| §7.4 异步补标注 | `backend/app/inference/tasks.py` | 持久任务 + worker 池 + 重试3次 dead + 孤儿回收 |
| §8 Concept 服务 | `backend/app/concept/service.py` | merge/undo/get_graph/increment_explore |
| undo 反向回放 | `backend/app/concept/audit.py` | audit log 快照回放 |
| 膨胀控制 | `qastep/repository.fork_child` + 触发器 | ≤6层 / ≤200概念，超限降级标注已有 |
| 前端 | `frontend/src/**` | 树状层级 + Cytoscape.js 力导向图 + 三状态着色 |

## 二、QAStep 状态机

```
generating --(流式回答完成)--> extracting --(概念标注完成)--> waiting
waiting --(点击概念下钻)--> generating   fork 新 QAStep, 挂 parent_qa_id
waiting --(回上层)--> waiting              栈式回退, 状态保留(不删子树)
waiting --(新问题)--> generating           开新探索树, parent_qa_id=NULL
```

- **乐观锁**：`qa_step.version`，写入 `WHERE version=$expected`，行数=0 抛 `OptimisticLockConflict`。
- **前端在途互斥**：`frontend/src/store/qaStore.js` 的 `inflight` 字段，同一 QAStep 未完成时禁用其他操作。
- **埋点**：`persist_telemetry` 写 model/prompt_hash/raw_output/parsed_concepts/aliases/confidence；归一化决策逐条写 `audit_log`（action=merge/keep/undo + candidate_name/matched_alias/similarity_score/llm_verdict）。评测可按 `qa_id` 回放并与 golden set 比对。

## 三、Concept 服务接口

| 接口 | 方法 | 路由 |
|-|-|-|
| `merge_concepts(id_a, id_b)` | 合并 b 入 a，aliases 合并 + 边迁移 + explore_count 累计，快照入 audit log | `POST /concept/merge` |
| `undo_merge(merge_id)` | 读 audit log 快照反向回放：还原 aliases、重建 absorbed 节点、边改回 | `POST /concept/undo` |
| `get_graph(session_id, origin_filter)` | 单表按 origin 派生三状态视图(user_click/co_occurrence/domain_graph) | `POST /concept/graph` |
| `increment_explore(concept_id)` | 热度+1，返回 color_tier | `POST /concept/explore` |

merge/undo 走全局串行锁 `_merge_lock`，防止与并发下钻写乱序。

## 四、归一化三级流水线

```
抽取概念 -> 别名精确匹配(@> JSONB)  命中->merge
        -> embedding 召回 top-k   >=high(0.92)->merge ; <low(0.78)->keep
        -> LLM 灰区判定           判定合并->merge / 判定保留->keep
        -> 全未命中->新建独立节点
全部决策只追加 audit_log
```

阈值见 `app/thresholds.local.json`（数据产品经理提供，热加载）。

## 五、推理协议与降级

- **Sentinel 分割**：模型先输出正文，以 `≡≡CONCEPT_BLOCK≡≡` 分隔，后接 ConceptBlock JSON。`StreamSplitter` 逐 token 检测 sentinel 行（行首行尾正则锚定，防正文误切分），切分后进入 JSON 累积模式。
- **InferenceSession**：Protocol 定义 `stream()` 产出 `{delta|sentinel|json_done|error}` 事件，QAStep 只消费语义、不感知推理细节。`StubInferenceSession` 为本地开发桩，真实实现由 Harness 工程师接入。
- **L0-L3 降级**：L1(JSON解析失败) 丢弃结构化只返回正文+后台异步补标注；L2 拆两次调用；L3 关键词匹配。正文一旦开始流式渲染不可重试。

## 六、膨胀控制

- 单树 ≤6 层（`MAX_EXPlore_DEPTH`）：超限 `fork_child` 抛 `DepthLimitReached`，API 返回 422，降级为只标注已有概念。
- 单会话 ≤200 概念（`MAX_CONCEPTS_PER_SESSION`）：`is_bloat_limit_reached` 命中则 `match_existing_only` 不新建。

## 七、运行

### 后端
```bash
cd backend
pip install -r requirements.txt
cp .env.example .env        # 配 DATABASE_URL 等
psql "$DATABASE_URL" -f migrations/001_init.sql
uvicorn app.main:app --reload --port 8000
# 测试（纯逻辑，不依赖 DB）
pytest tests/ -q
```

### 前端
```bash
cd frontend
npm install
npm run dev    # http://localhost:5173 (proxy → :8000)
```

## 八、接口依赖对齐（跨模块）

| 依赖 | 提供方 | 本仓库的对接点 | 状态 |
|-|-|-|-|
| 单次调用流式正文+尾部JSON协议 | 推理框架工程师 | `inference/protocol.py` 的 `InferenceSession` + `StreamSplitter` + Sentinel 约定 | 协议已定义，待接入真实推理框架 |
| InferenceSession 封装 | Harness 工程师 | `InferenceSession` Protocol，`StubInferenceSession` 桩 | 边界已定义，待 Harness 替换桩 |
| 归一化阈值 + golden clusters | 数据产品经理 | `concept/thresholds.py` 热加载 + 门禁阈值 | 阈值默认值已落地，golden clusters 待提供(不阻塞主链路) |

## 九、已知缺口 / 待补

1. **PRD 文档未能读取**：bot 对 PRD 无查看权限(OpenAPI 3380004)。本实现以技术架构文档为唯一依据；PRD 中的产品级 UX 细节(如 5 个试点领域、具体 golden test set)未纳入，需 PRD 授权后补充。
2. **golden concept clusters 待数据产品经理提供**（技术文档第十节标注「待提供」），归一化准确率评测门禁(误合并率<5%、Recall>85%)需其到位后校准。
3. **Embedder/LLMJudge 为桩实现**：`MockEmbedder`(编辑距离近似)/`MockLLMJudge`，生产替换为真实向量召回与轻量 LLM 调用。
4. **SSE 重连**：客户端 `EventSource` onerror 仅关闭，harness 凭 `last-event-id` 续推的完整重连协议需 Harness 实现补齐（协议边界已定义 `answer_offset` checkpoint）。



## 十、真实推理链路接入（TASK-56 / TASK-57 集成）

上游推理框架与 Harness 已接入主仓（上游代码包 bot 无权下载，按架构文档+协议规格等价自行实现，差异见 `INTEGRATION_NOTES.md`）。

### 模块映射

| 上游交付物 | 本仓模块 | 说明 |
|-|-|-|
| 真实 InferenceClient（TASK-56） | `app/inference/{sentinel,constraints,context,backend,client}.py` | SentinelDetector 滑动窗口+前缀缓冲；L0-L3 降级；约束解码探测；上下文 ≤2K token |
| 生产级 InferenceSession（TASK-57） | `app/harness/{inference_session,manager,recovery,store,circuit_breaker,reannotation,observability,app}.py` | checkpoint 恢复；熔断 failover；异步补标注 worker；/harness/obs/metrics |
| 评测埋点 NDJSON | `app/qastep/telemetry.py` | 11 字段，评测管线按 qa_id 回放 |
| 评测管线 | `eval/` + `scripts/export_telemetry.py` | 埋点→replay 桥接 |

### 接入方式

- QA 路由 `routes_qa._pipeline_for` 改为 `get_harness().session_for(...)`，QAStepPipeline 无需改动（InferenceSession 实现既有 Protocol）。
- 真实推理服务：配置 `LLM_BASE_URL` / `LLM_API_KEY` / `LLM_MODEL` 即启用 `OpenAICompatibleBackend`；未配置时 `StubLLMBackend` 兜底，全链路独立可运行。
- 可观测：`GET /harness/obs/metrics` 返回中断恢复/熔断/补标注/回填延迟四项指标 + 门禁值。

### 一键启动

```bash
./start.sh                      # 后端 :8000 + 前端 :5173（stub 兜底）
# 或 docker-compose up          # PG + 后端 + 前端，自动跑 001/002 迁移
```

### 测试与评测

```bash
cd backend && pytest tests/ -q                              # 46 passed
cd .. && python eval/scripts/run_eval.py --mode extraction --golden-set eval/golden_set/
python scripts/export_telemetry.py --telemetry-dir telemetry/qa_steps --out telemetry.json
python eval/scripts/run_eval.py --mode replay --golden-set eval/golden_set/ --telemetry telemetry.json
```

### 已知缺口更新（相对原第九节）

- **SSE 重连**：harness 已提供 `resume(qa_id)` checkpoint 续推（`InMemoryCheckpointStore` 默认 / `SqlCheckpointStore` + `migrations/002_harness.sql`）；前端 `last-event-id` 重连协议待前端补齐调用。
- **PRD / golden clusters**：仍待授权 / 待数据产品经理提供，不阻塞主链路与评测管线框架。
