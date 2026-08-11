"""流式中断状态恢复（技术架构文档 7.2 / Harness 设计规格 §二）。

两类中断：
- 用户主动回上层：harness 收取消事件 -> 发 abort，已流出正文与概念落盘保留
  （用户回到该层应看到完整现场而非空白重来）。
- 网络断开：SSE 断连后前端凭 last-event-id 重连，harness 从最近 checkpoint 续推，
  推理调用本身不重启。

恢复协议以 qa_id + offset 为准，前端丢弃本地未确认内容。
resume 未知 qa 返回 status=="unknown" 的统一结构（recovery 层据此判 clean failure）。
"""
from __future__ import annotations

import logging
from typing import Optional

from app.harness.models import Checkpoint, ResumeResult, SessionStatus
from app.harness.store import InMemoryCheckpointStore, CheckpointStore

log = logging.getLogger(__name__)


class RecoveryManager:
    """中断恢复：checkpoint 保存 + resume 续推。"""

    def __init__(self, store: CheckpointStore):
        self.store = store

    async def save_checkpoint(self, cp: Checkpoint) -> None:
        await self.store.save(cp)

    async def resume(self, qa_id: str) -> ResumeResult:
        """续推：返回最近 checkpoint；未知 qa 返回 status=unknown。"""
        return await self.store.resume(qa_id)

    async def on_user_back(self, qa_id: str) -> Optional[Checkpoint]:
        """用户回上层：状态置 interrupted，正文保留。"""
        cp = await self.store.get(qa_id)
        if cp is None:
            return None
        cp.status = SessionStatus.INTERRUPTED
        await self.store.save(cp)
        return cp

    async def complete(self, qa_id: str) -> None:
        cp = await self.store.get(qa_id)
        if cp:
            cp.status = SessionStatus.COMPLETED
            await self.store.save(cp)
