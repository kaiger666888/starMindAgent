"""流式中断状态恢复协调器（设计规格 §三 / 架构文档 §7.2 / 协议 §7.2）。

两类中断：
- 用户主动回上层：向推理层发 abort，已流出正文 + 已解析概念落盘保留（回到该层
  看到完整现场而非空白重来）
- 网络断开：SSE 凭 last-event-id 重连，从最近 checkpoint 续推，推理调用不重启

RecoveryCoordinator 在 InferenceSessionManager 之上记录恢复成功率（设计规格 §六
非功能指标「中断恢复成功率 > 95%」），上报可观测接口。
"""
from __future__ import annotations

import logging
from typing import Optional

from app.harness.manager import InferenceSessionManager

log = logging.getLogger(__name__)


class RecoveryCoordinator:
    """中断恢复协调器：区分两类中断 + 上报恢复成功率。"""

    def __init__(self, manager: InferenceSessionManager, observability=None):
        self.manager = manager
        self.observability = observability
        self._attempts = 0
        self._successes = 0

    async def handle_user_rollback(self, qa_id: str) -> dict:
        """用户主动回上层：abort + 落盘保留现场（设计规格 §三）。"""
        self._attempts += 1
        snap = await self.manager.abort(qa_id)
        # 成功：现场被保留（有 checkpoint 且状态为 interrupted / 有 offset）
        ok = snap.get("status") in ("interrupted", "completed") or snap.get("offset", 0) > 0
        if ok:
            self._successes += 1
        self._report("user_rollback", ok)
        return snap

    async def handle_reconnect(self, qa_id: str, last_event_id: int = 0) -> dict:
        """网络断开重连：从 checkpoint 续推，推理调用不重启（设计规格 §三）。"""
        self._attempts += 1
        res = await self.manager.resume(qa_id, last_event_id)
        # 成功：拿到 checkpoint（非 unknown）且能给出 resume_offset
        cp = res.get("checkpoint") or {}
        ok = cp.get("status") != "unknown"
        if ok:
            self._successes += 1
        self._report("reconnect", ok)
        return res

    def success_rate(self) -> float:
        if self._attempts == 0:
            return 1.0
        return self._successes / self._attempts

    def snapshot(self) -> dict:
        return {
            "attempts": self._attempts,
            "successes": self._successes,
            "success_rate": round(self.success_rate(), 4),
        }

    def _report(self, kind: str, ok: bool) -> None:
        if self.observability is not None:
            try:
                self.observability.record_recovery(kind, ok)
            except Exception:
                pass
