"""Harness 生命周期层（对应 Harness 工程师交付物）。

技术架构文档第七节 + Harness 设计规格文档：
- InferenceSession 封装：流式读取 + sentinel 检测 + 降级判定，封装推理层细节，
  QAStep 只感知业务语义事件（{delta|sentinel|json_done|error}）。
- 流式中断状态恢复：用户主动回上层 -> 已流出正文落盘保留 + 发 abort；
  网络断开 -> SSE 凭 last-event-id 重连，从最近 checkpoint 续推。
- 熔断重试：按模型端点维度统计错误率；首 token 超时 5s 重试一次、整体 60s 熔断进 L1、
  JSON 15s 上限；连续失败切备用端点；正文一旦开始流式渲染只降级不重试。
- 异步补标注 worker：待补标注落库为持久任务（qa_id+状态+重试计数），后台 worker 池消费，
  成功回填 concept_ids 触发前端增量刷新，重试 3 次失败标 dead，孤儿任务回收；
  归一化调用强制串行化。
- 可观测接口：GET /harness/obs/metrics 暴露中断恢复成功率 / 熔断 / 补标注 / 回填延迟。

模块映射（设计规格 §一）：
  inference_session.py + manager.py   ① InferenceSession 抽象
  recovery.py + store.py              ② 流式中断状态恢复
  circuit_breaker.py                   ③ 熔断与重试
  reannotation.py                     ④ L1 异步补标注生命周期
  observability.py                     ⑤ 可观测接口
  app.py                               build_harness 装配
"""
