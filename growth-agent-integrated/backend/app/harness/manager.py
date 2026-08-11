"""InferenceSession 生命周期管理（每 QAStep 一条会话记录）。"""
from __future__ import annotations

import logging
from typing import Optional

from app.harness.inference_session import InferenceSession
from app.harness.store import InMemoryCheckpointStore, CheckpointStore
from app.harness.recovery import RecoveryManager
from app.harness.circuit_breaker import CircuitBreaker
from app.inference.backend import LLMBackend, StubLLMBackend
from app.inference.client import InferenceClient

log = logging.getLogger(__name__)


class SessionManager:
    """按 qa_id 创建/缓存 InferenceSession。"""

    def __init__(self, backend: LLMBackend | None = None,
                 checkpoint_store: CheckpointStore | None = None,
                 breaker: CircuitBreaker | None = None,
                 recovery: RecoveryManager | None = None,
                 backfill_hook=None):
        self.backend = backend or StubLLMBackend()
        self._cp_store = checkpoint_store or InMemoryCheckpointStore()
        self._breaker = breaker
        self._recovery = recovery or RecoveryManager(self._cp_store)
        self._backfill_hook = backfill_hook
        self._sessions: dict[str, InferenceSession] = {}

    def session_for(self, qa_id: str, session_id: str, question: str) -> InferenceSession:
        if qa_id in self._sessions:
            return self._sessions[qa_id]
        client = InferenceClient(qa_id, session_id, question, backend=self.backend,
                                backfill_hook=self._backfill_hook)
        sess = InferenceSession(qa_id, session_id, question, client=client,
                                checkpoint_store=self._cp_store,
                                breaker=self._breaker, recovery=self._recovery)
        if self._breaker:
            sess.attach_breaker(self._breaker)
        self._sessions[qa_id] = sess
        return sess

    def get(self, qa_id: str) -> Optional[InferenceSession]:
        return self._sessions.get(qa_id)

    async def abort(self, qa_id: str) -> None:
        sess = self._sessions.get(qa_id)
        if sess:
            await sess.abort()
