# Harness 生命周期层 —— 生产级实现

替换设计规格阶段的参考实现，可被 Agent 研发工程师主仓直接集成。对应设计规格文档 §一 的五个交付项。

## 交付项 → 文件映射

| 交付项 | 模块 | 关键类 |
| --- | --- | --- |
| ① InferenceSession 抽象 | `inference_session.py` / `manager.py` | `InferenceSession`、`InferenceSessionManager` |
| ② 流式中断状态恢复 | `recovery.py` / `store.py` | `RecoveryCoordinator`、`CheckpointStore` |
| ③ 推理调用熔断与重试 | `circuit_breaker.py` | `CircuitBreaker`、`CircuitBreakerRegistry`、`ResilientCaller`、`RetryPolicy` |
| ④ L1 异步补标注生命周期 | `reannotation.py` | `WorkerPool`、`InMemoryTaskStore`、`SqlTaskStore` |
| ⑤ 非功能指标可观测接口 | `observability.py` | `MetricsCollector`、`observability_router` |

辅助模块：`models.py`（数据模型/枚举）、`sentinel.py`（跨 chunk sentinel 切分 + JSON 累积）、`inference_client.py`（推理框架契约 Protocol + 桩）、`app.py`（`build_harness` 装配）。

## 快速集成

主仓 `api/routes_qa.py` 接入方式：

```python
from app.harness import build_harness

# 注入推理框架工程师的真实 InferenceClient（见下节）
harness = build_harness(client=real_client)

# 挂载可观测 router
for r in harness.routers():
    app.include_router(r)

# 启动后台补标注 worker 池（FastAPI lifespan）
@asynccontextmanager
async def lifespan(app):
    harness.worker_pool.start()
    yield
    await harness.worker_pool.stop()
app = FastAPI(lifespan=lifespan)

# 一次 QA 推理
sess = await harness.manager.start(session_id, qa_id, question, model)
pipe = QAStepPipeline(qa_id, session_id, question, sess, normalizer, repo)
async for ev in pipe.run():   # QAStepPipeline 直接消费 sess.stream()
    ...                        # ev: {delta | sentinel | json_done | error}
```

`InferenceSession` 实现主仓已有的 `InferenceSession` Protocol（`stream() -> {kind:"delta"|"sentinel"|"json_done"|"error"}`），`QAStepPipeline` 无需改动。

## InferenceClient 注入点

推理框架工程师实现以下 Protocol，通过 `build_harness(client=...)` 注入：

```python
class InferenceClient(Protocol):
    async def stream(self, req: InferenceRequest) -> AsyncIterator[StreamChunk]: ...
    async def abort(self, call_id: str) -> None: ...
    async def extract_only(self, text: str, model: str | None = None) -> ConceptBlock: ...
```

- `stream` 产出原始 token 流（**含 sentinel**，由 Harness 切分），每个 `StreamChunk` 自带 `call_id` 供 `abort`。
- `abort` 向推理层发取消（用户回上层时调用）。
- `extract_only` 轻量抽取调用，L2 拆分与 L1 异步补标注复用同一入口。

数据契约（`models.py`）：

- `InferenceRequest`：`prompt`、`endpoint`（熔断维度 key）、`model`、`temperature`、`resume_offset`（断连续推跳过已落盘正文 prefix）。
- `StreamChunk`：`call_id`、`delta`、`finish_reason`（`"stop"|"length"|None`）。
- `ConceptBlock` / `ConceptItem`：复用主仓 `app.schemas`。

联调期可用 `StubInferenceClient`（`_Script` 可编排首 token 延迟 / chunk 间隔 / JSON 失败 / 无 sentinel / 流异常 / extract 失败 / resume，覆盖 L0–L3 与超时路径）。

## 分层超时与熔断重试

分层超时（`HarnessTimeouts`，对齐协议 §7.3）：

- 首 token `5s` → 超时**重试一次**（换端点）。
- 整体 `60s` → 熔断进 L1。
- 结构化 JSON `15s` → 按 L1 处理。

熔断（`CircuitBreaker`）按**端点维度**统计：连续失败 N 次 / 错误率阈值触发 `OPEN` → 冷却后 `HALF_OPEN` 探测 → 成功复位 / 失败重 trip。`ResilientCaller` 按 `failover_map` 选端点，全部 `OPEN` 时退回主端点拿真实错误。

重试策略（`RetryPolicy`）核心约束：**正文一旦开始流式渲染即不可撤回**——首 token 之后任何失败只降级（L1）、不重试，避免重复推送正文。

## 中断恢复协议（§三）

两类中断，恢复语义不同：

- **用户主动回上层**（`manager.abort` / `recovery.handle_user_rollback`）：向推理层发 `abort`，已流出正文 + 已解析概念**落盘保留**（状态 `interrupted`），回到该层看到完整现场而非空白重来。
- **网络断开重连**（`manager.resume` / `recovery.handle_reconnect`）：SSE 凭 `last-event-id` 重连，从最近 checkpoint 续推，**推理调用不重启**（`resume_offset` 跳过已落盘正文 prefix）。返回 `checkpoint` 快照 + `events`（`answer_replay` / `concepts_replay`）+ `resume_offset`；无 checkpoint 时返回 `checkpoint.status == "unknown"`（clean failure）。

checkpoint 每 `checkpoint_every`（默认 8）个事件增量落盘（`CheckpointStore`：内存 / `SqlCheckpointStore` 写 `harness_checkpoint` 表）。

## L1 异步补标注生命周期（§五）

L1 降级经 `on_degrade` 回调 → `InferenceSessionManager._on_degrade` → `WorkerPool.enqueue_reannotation`，落库为持久任务：

- `claim` 原子化（内存 `asyncio.Lock` / Sql `FOR UPDATE SKIP LOCKED`），worker 重启从 DB 恢复。
- 回填前校验目标 QAStep 存在性，**孤儿任务直接回收**（状态 `reclaimed`，非 dead）。
- **重试 3 次仍失败标 dead**，前端静默保持无标注。dead-letter 阈值由 `store.max_retry` 统一持有（默认 `settings.backfill_max_retry=3`，可经 `InMemoryTaskStore(max_retry=N)` / `SqlTaskStore(scope, max_retry=N)` 注入）。
- 归一化（merge/undo）走同一任务通道（`kind` 区分），按 **session 维度强制串行化**（`_norm_locks`）。
- 启动时 `reset_running` 回收 stale running 任务 + `reclaim_orphans` 批量回收。

处理器与归一化执行器以依赖注入接入（`make_default_backfill_handler` / `make_default_normalization_handler` 默认接主仓 `app.concept` / `app.qastep`，单测可用桩替换，不依赖 DB）。

## 可观测接口 /harness/obs/metrics

`GET /harness/obs/metrics` 返回四项核心指标 + 熔断快照，门禁值内联（AI 评测工程师可直接对接）：

```json
{
  "interruption_recovery": {
    "success_rate": 0.9733, "gate": "> 95%", "attempts": 150
  },
  "circuit_breaker": {
    "error_rate_per_endpoint": {"primary": 0.02, "backup": 0.0},
    "states": {"primary": "CLOSED", "backup": "CLOSED"},
    "snapshots": {"primary": {"state":"CLOSED","error_rate":0.02,"failures":3,"successes":147}},
    "alert_threshold": "> 10%/h"
  },
  "async_reannotation": {
    "completion_rate": 0.96, "dead_letter_rate": 0.01,
    "gate": "completion > 90%, dead_letter < 2%"
  },
  "backfill_latency": { "p95_ms": 4200, "gate": "< 30000 ms" }
}
```

字段粒度：

- **中断恢复成功率**：`success_rate`（`RecoveryCoordinator` 累计 attempts/successes）；门禁 `> 95%`。
- **熔断**：`error_rate_per_endpoint` + `states` 按端点维度；`snapshots` 含每端点 `state/error_rate/failures/successes`；告警阈值 `> 10%/h`。
- **异步补标注**：`completion_rate` + `dead_letter_rate`（`WorkerPool` 累计 completed/dead）；门禁 completion `> 90%`、dead_letter `< 2%`。
- **回填延迟**：`p95_ms`（`WorkerPool` 滑动采样最近 1000 次）；门禁 `< 30000 ms`。

另提供 `GET /harness/obs/health` 健康检查。

## 配置项（环境变量，`app/config.py`）

- `CONCEPT_SENTINEL`（默认 `≡≡CONCEPT_BLOCK≡≡`）：sentinel 基串，Harness 包成独占一行。
- `INFERENCE_TIMEOUT_S` / `FIRST_TOKEN_TIMEOUT_S` / `JSON_PARSE_TIMEOUT_S`：分层超时（默认 60 / 5 / 15）。
- `BACKFILL_MAX_RETRY`（默认 3）：补标注 dead-letter 阈值。
- `DATABASE_URL`：Postgres 连接（`SqlCheckpointStore` / `SqlTaskStore`）。

## 迁移

`migrations/002_harness.sql`：`harness_checkpoint` 表（checkpoint 增量 upsert）+ `harness_task` 表（持久补标注任务，含 `reclaimed` 状态、`FOR UPDATE SKIP LOCKED` claim）。主仓已有 `migrations/001_init.sql`。

## 测试

```bash
cd backend
python3 -m pytest tests/test_*.py -q
```

38 个 Harness 测试 + 11 个主仓已有测试，全绿。覆盖：跨 chunk sentinel 切分 / 花括号提取 / fenced markdown；连续失败·错误率·半开恢复·探测重 trip·failover·全 OPEN 退回·正文已开始阻断重试；L0/L1 结构化重试恢复·L1 双失败入队·无 sentinel·首 token 重试成功/失败·整体超时只降级·流异常只降级·abort 保留·熔断切备用端点；回上层持久化+重连续推·未知 qa 干净失败·完成后重放 concepts·进程重启重水化；成功回填刷新·重试 3→dead·孤儿 reclaimed·批量回收·归一化同 session 串行/不同 session 并行·stale running 重置；指标字段与门禁·router 端点·端点维度快照粒度。
