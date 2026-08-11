# InferenceClient — 真实推理会话实现

替换主仓 `app/inference/protocol.py` 中的 `StubInferenceSession` 桩，让 QAStep 跑在真实推理流上。
实现以《流式+结构化输出协议与 L0-L3 降级链路》技术设计文档和《「伴你成长」学习 Agent — 技术架构文档》为唯一依据。

## 文件结构

```
backend/app/inference/
├── __init__.py        # 合并导出：保留原有 protocol/tasks，追加真实实现
├── protocol.py        # 主仓原有（未改动）：InferenceSession Protocol + Stub 桩
├── tasks.py           # 主仓原有（未改动）：异步补标注 worker
├── sentinel.py        # 新增：SentinelDetector 跨 chunk 检测器（协议 3.2）
├── constraints.py     # 新增：约束解码 + 正则/Pydantic 降级 + L3 关键词兜底（协议 四、5.2）
├── context.py         # 新增：ContextBudget 上下文膨胀控制，深层 prompt ≤ 2K token（协议 六）
├── backend.py         # 新增：LLMBackend 抽象 + OpenAI 兼容流式后端 + 测试桩
└── client.py          # 新增：InferenceClient 真实实现 + L0-L3 降级链路（协议 五）

backend/tests/inference/
├── test_sentinel.py       # sentinel 跨 chunk 边界检测
├── test_constraints.py    # 约束解码 / 正则提取 / 关键词兜底
├── test_context.py        # 上下文膨胀控制与压缩
└── test_client.py         # L0-L3 降级路径覆盖
```

## 接口对齐

`InferenceClient` 实现主仓 `app.inference.protocol.InferenceSession` Protocol：

| Protocol 要求 | InferenceClient |
|---|---|
| `session_id: str` | ✅ 构造参数 |
| `qa_id: str` | ✅ 构造参数 |
| `async def stream() -> AsyncIterator[dict]` | ✅ |

`stream()` 产出的事件种类与主仓 Protocol 桩完全一致，QAStepPipeline 无需改动即可消费：

| kind | payload | 说明 |
|---|---|---|
| `delta` | `{text}` | 正文增量（逐 token 渲染） |
| `sentinel` | — | 正文结束，进入 JSON 累积（QAStep generating→extracting） |
| `json_done` | `{block}` | 尾部 JSON 解析完成（ConceptBlock）→ L0 |
| `error` | `{message, level, needs_backfill}` | 失败 → L1/L3；`level`/`needs_backfill` 为额外观测字段，QAStepPipeline 只读 `message`，不影响消费 |

注入替换（Agent 研发侧）：

```python
from app.inference.client import InferenceClient
from app.inference.backend import OpenAICompatibleBackend

backend = OpenAICompatibleBackend(base_url="http://localhost:8000/v1", api_key="sk-...")
session = InferenceClient(
    qa_id=qa_id, session_id=session_id, question=question,
    backend=backend, model="qwen2.5-32b",
    parent_chain=["神经网络"],            # 上层概念链（膨胀控制用）
    concept_table=preset_concept_table,   # L3 关键词兜底用别名表
    backfill_hook=backfill_queue.enqueue,  # L1 异步补标注落库
)
pipeline = QAStepPipeline(qa_id, session_id, question, session, normalizer, repo)
```

## 降级行为（协议第五节）

| 级别 | 触发条件 | 行为 | 事件序列 |
|---|---|---|---|
| **L0** | 单次调用成功产出正文 + 结构化概念 | 全链路无阻塞 | `delta*` / `sentinel` / `json_done` |
| **L1** | JSON 解析失败（含超时 15s / 整体超时 60s），重试也失败 | 丢弃结构化部分，只返回正文；`backfill_hook` 落库待补标注 | `delta*` / `sentinel` / `error(L1)` |
| **L2** | 模型不支持流式+结构化并存（`co_streaming_supported=False`） | 拆两次调用：先流式回答，再轻量抽取（可用更小模型） | `delta*` / `sentinel` / `json_done` |
| **L3** | L2 第二次抽取也失败 | 关键词匹配兜底（预置别名表子串匹配）；命中则出概念，未命中则 `error(L3)` | `delta*` / `sentinel` / `json_done` 或 `error(L3)` |

关键约束（协议 4.3）：正文一旦开始流式渲染不可撤回，任何失败只能降级、不能整次重试。重试仅限：
- 首 token 超时（正文未渲染，幂等重试一次）；
- 结构化部分解析失败（重抽概念块，正文不动，至多 1 次，temperature 降 0.2）。

## 约束解码（协议 4.1-4.2）

- **主路径**：outlines / xgrammar 约束解码，作用于纯 JSON 抽取调用（L2 第二次调用 / 重试调用），通过 `guided_json`（vLLM）或 `response_format` json_schema（OpenAI 兼容）从生成层消除格式错误。
- **sentinel 触发分段约束**需引擎运行时激活/去激活 grammar，仅在自托管 vLLM/Optimum + outlines/xgrammar 场景可用（`ConstrainedDecoder` 自动探测安装）。标准 API 场景退化为正则路径。
- **降级路径**：正则提取 JSON 块（最外层花括号配对）→ Pydantic 校验 → 失败单次重试。

## 上下文膨胀控制（协议六）

`ContextBudget` 构造深层 prompt，总 token ≤ 2K：
- system prompt（协议约束）~400 token；
- 上层概念链摘要 ~1000 token（每层 canonical_name + 一句话定位）；
- 当前问题 ~400 token；
- 余量 ~200 token。

超限时从最上层逐层压缩（去掉 one_liner 只留 canonical_name），仍超限则丢弃非当前分支兄弟概念，只保留当前下钻路径主干。token 估算优先用 tiktoken，不可用时按字符数 × 0.6 粗估。

## Sentinel 检测（协议 3.2）

`SentinelDetector` 按协议 3.2 的「滑动窗口 + sentinel 前缀缓冲」算法实现跨 chunk 边界匹配：
- sentinel = `\n≡≡CONCEPT_BLOCK≡≡\n`（精确字符串，core 来自 `settings.concept_sentinel`）；
- feed 时用 `find(sentinel)` 检测完整 sentinel，未命中时只发出「确认非 sentinel 前缀」的前缀，尾部保留可能是 sentinel 前缀的部分（缓冲上限 len(sentinel)-1，延迟有界）；
- 快路径：缓冲中既无换行也无 `≡` 时立即全发出，正常正文无延迟；
- 命中后剩余 token 直接转入 JSON 累积缓冲，不丢 token；
- flush 行级兜底：流结束时若 sentinel 尾随换行缺失，用行级正则补判一次。

> 注：sentinel 必须为 `\n{core}\n` 独占一行（协议 2.4 要求模型如此输出）。正文内嵌的零散 `≡` 不会触发切分。

## 超时对齐（协议 7.3）

| 场景 | 超时 | 动作 |
|---|---|---|
| 流式首 token | 5s | 重试一次（幂等，正文未渲染） |
| 整体调用 | 60s | 熔断进入 L1，已产出正文保留 |
| 结构化 JSON | 15s | 按 L1 处理 |

超时值从 `app.config.settings` 读取，与 Harness 对齐。

## 测试

```bash
cd backend
pip install -r requirements.txt -r requirements.inference.txt
pytest tests/inference/ -v --asyncio-mode=auto
```

已验证的降级路径（47 个测试全通过）：
- sentinel 跨 chunk 任意位置切断（step=1/2/3/5/7/11/13）均正确检测，正文不泄漏；
- L0 单次调用成功 / L0 跨 chunk / L0 经重试成功；
- L1 JSON 解析失败 + 重试失败 / L1 无 sentinel / L1 整体超时；
- L2 拆两次调用成功；
- L3 关键词命中 / L3 未命中；
- 上下文膨胀压缩（深层链 / 丢弃非当前分支兄弟）；
- Protocol 属性与事件种类校验；
- 主仓原有 11 个测试无回归。

## 依赖

`requirements.inference.txt`：
- `httpx`（OpenAI 兼容流式后端，已在主仓环境）
- `pydantic>=2.6`（ConceptBlock 校验，主仓已有）
- 可选：`tiktoken`（精确 token 估算，不可用时退化为字符估算）
- 可选：`outlines` / `xgrammar`（本地引擎约束解码，不可用时退化为正则路径）
