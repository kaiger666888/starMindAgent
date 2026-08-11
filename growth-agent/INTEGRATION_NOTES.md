# 集成说明 — 真实推理链路接入与上游实现差异

> 本轮把推理框架（TASK-56 真实 InferenceClient）与 Harness（TASK-57 生产级 InferenceSession）
> 接入主仓，替换全部 stub，打通「前端 → FastAPI → QAStep 状态机 → Concept 服务 → 推理/降级 → 评测埋点」端到端链路。

## 1. 背景：为什么是等价自行实现

上游 TASK-56 / TASK-57 两个代码包 bot 无下载权限（HTTP 403），用户确认按
**架构文档 + 协议规格等价自行实现（含 stub 兜底）**，并标注与上游实现的差异。
本仓两份新模块（`app/inference/{sentinel,constraints,context,backend,client}.py`、
`app/harness/*`）即依据以下两份**已读取**的规格等价实现：

- 技术架构文档（§6 推理协议、§7 Harness、§9.4 非功能门禁）
- 上游任务评论中给出的协议规格（TASK-56：sentinel 三重防护/ConceptBlock Schema/
  流式四态状态机/跨 chunk 检测/L0-L3 降级/上下文膨胀 ≤2K；TASK-57：InferenceSession
  封装/checkpoint qa_id+offset/熔断按端点维度+failover/异步补标注生命周期/
  /harness/obs/metrics 字段粒度）

> 未读取到上游代码包本体，故模块名与文件粒度尽量对齐上游交付物描述
> （`app/inference/{sentinel,constraints,context,backend,client}.py`、
> `app/harness/{inference_session,manager,recovery,store,circuit_breaker,reannotation,
> observability,app}.py` + `migrations/002_harness.sql`），但**内部实现细节为等价复现**。

## 2. stub 替换清单

| 主仓原桩 | 替换为（真实实现） | 接入点 |
|-|-|-|
| `inference/protocol.py::StubInferenceSession` | `app/harness/inference_session.py::InferenceSession`（包裹 `app/inference/client.py::InferenceClient`） | `routes_qa._pipeline_for` 改为 `get_harness().session_for(...)` |
| `inference/tasks.py::backfill_queue`（进程内队列） | `app/harness/reannotation.py::WorkerPool`（持久任务 + claim + 重试3→dead + 孤儿回收） | `main.py` lifespan 启动 harness worker；`backfill_queue` 保留为兼容旧入口 |
| `inference/protocol.py::StreamSplitter`（桩检测） | `app/inference/sentinel.py::SentinelDetector`（滑动窗口+前缀缓冲，缓冲精确到 len(sentinel)-1） | `InferenceClient` 内部使用；`StreamSplitter` 保留（基线测试引用，行为不变） |
| Embedder/LLMJudge 桩（归一化） | 保留为桩（归一化阈值/golden clusters 待数据产品经理提供，不阻塞主链路） | — |

真实推理服务接入：配置 `LLM_BASE_URL` / `LLM_API_KEY` / `LLM_MODEL` 环境变量即启用
`OpenAICompatibleBackend`；未配置时 `default_backend()` 退化 `StubLLMBackend`，全链路仍可独立运行演示。

## 3. 端到端链路验证

`pytest backend/tests/ -q` → **46 passed**（11 既有基线 + 35 新增集成/降级/熔断/补标注/可观测）。

- **L0 主路径**：`tests/test_integration.py::test_e2e_l0_with_harness_session`
  harness InferenceSession + stub backend → `generating→extracting→waiting`，
  产出 `concepts` + `done`，11 字段埋点 NDJSON 落盘。
- **L1 降级**：`test_e2e_l1_degradation_keeps_prose` 正文流出后 JSON 失败 →
  丢弃结构化只返回正文，仍进 waiting（不阻塞主流程）。
- **降级路径**：`tests/inference/test_client.py` 覆盖 L0 / L1(backfill) / L2 拆两次调用 / L3 兜底命中与未命中。
- **sentinel 跨 chunk**：`tests/inference/test_sentinel.py` 在 step=1/2/3/5/7/11/13 任意切断均正确检测，正文不泄漏 sentinel。
- **Harness**：checkpoint resume（含未知 qa clean failure）、熔断 open/half-open/failover、补标注 retry3→dead + 孤儿回收、/harness/obs/metrics 四项指标 + 门禁。
- **评测管线**：`eval/scripts/run_eval.py --mode extraction --golden-set eval/golden_set/` 可跑通；
  埋点→评测桥接：`scripts/export_telemetry.py` 聚合 NDJSON 为 replay JSON。

## 4. 与上游实现的可能差异（因未读到上游代码包本体）

1. **约束解码**：上游规格提及 outlines/xgrammar 优先、不支持时退化正则+Pydantic+单次重试（temperature 降 0.2）。
   本实现做了后端探测（`guided_backend_name()`）与退化路径（`parse_concept_block` + `_retry_extract_once`），
   但**未在沙箱安装 outlines/xgrammar**（探测返回 None 走退化）；真实部署安装后探测自动启用，
   但约束解码作用于纯 JSON 抽取调用（L2 第二次/重试）的具体注入点需与上游对齐确认。
2. **熔断 failover**：本实现 failover 链由 `CircuitBreaker(failover_chain=[...])` 配置，
   默认仅 primary；上游可能从 settings 读取备用端点列表（`LLM_BACKUP_MODEL` 已预留）。
3. **checkpoint 持久化**：提供 `InMemoryCheckpointStore`（默认）与 `SqlCheckpointStore`
   （`migrations/002_harness.sql`）；上游 `SqlCheckpointStore` 的列名/语义可能不同，
   跨进程续推需以本仓 002 表为准。
4. **补标注处理器**：默认 `_default_processor` 复用主仓 `backfill_processor`（L3 关键词匹配兜底）；
   上游可能接独立抽取模型——`backend.extract_only` 即注入点，替换实现即可。
5. **OpenAI 兼容后端**：`OpenAICompatibleBackend.stream` 用 `httpx.AsyncStream`；
   上游 SDK/网关实现可能不同，但都满足 `LLMBackend` Protocol（stream/extract_only/abort）。

## 5. 给 AI 评测工程师的入口与数据路径

- 埋点 NDJSON：`TELEMETRY_DIR`（默认 `telemetry/qa_steps/<date>/<qa_id>.jsonl`），11 字段，按 qa_id 回放。
- 桥接：`python scripts/export_telemetry.py --telemetry-dir telemetry/qa_steps --out telemetry.json`
- 回放比对：`python eval/scripts/run_eval.py --mode replay --golden-set eval/golden_set/ --telemetry telemetry.json`
- 非功能门禁：`GET /harness/obs/metrics`（中断恢复>95% / 补标注>90% / dead letter<2% / 回填 P95<30s / 熔断>10%/h 告警）。
- 归一化准确率评测需 golden concept clusters（数据产品经理待提供），管线框架已就绪。
