"""Checkpoint 持久化（设计规格 §三 / 协议 §7.2）。

恢复协议以 qa_id + offset 为准。CheckpointStore 持久化每个 QAStep 的流式现场：
session_id + qa_id + answer_checkpoint + sentinel_position + json_state + status +
offset + last_event_id + concept_ids。

提供两个实现：
- InMemoryCheckpointStore：单测 / 进程内恢复用，不依赖 DB
- SqlCheckpointStore：生产用，Postgres（asyncpg）upsert，DDL 见 migrations/002

进程重启后从持久化 checkpoint 重水化，推理调用不重启（正文已落盘）。
"""
from __future__ import annotations

import json
from typing import Optional, Protocol

from app.harness.models import Checkpoint


class CheckpointStore(Protocol):
    async def save(self, cp: Checkpoint) -> None: ...
    async def load(self, qa_id: str) -> Optional[Checkpoint]: ...
    async def delete(self, qa_id: str) -> None: ...


class InMemoryCheckpointStore:
    """进程内 checkpoint 存储（单测 / 内存恢复）。"""

    def __init__(self):
        self._data: dict[str, Checkpoint] = {}

    async def save(self, cp: Checkpoint) -> None:
        self._data[cp.qa_id] = cp

    async def load(self, qa_id: str) -> Optional[Checkpoint]:
        return self._data.get(qa_id)

    async def delete(self, qa_id: str) -> None:
        self._data.pop(qa_id, None)


class SqlCheckpointStore:
    """Postgres checkpoint 存储（生产，asyncpg 经 SQLAlchemy）。

    表结构（migrations/002_harness.sql）：
      harness_checkpoint(qa_id pk, session_id, answer_checkpoint, sentinel_position,
        json_state, status, degrade_level, offset, last_event_id,
        concept_ids jsonb, call_id, endpoint, raw_json, updated_at)
    """

    TABLE = "harness_checkpoint"

    def __init__(self, session_scope):
        # session_scope: app.db.session_scope（依赖注入，便于测试解耦）
        self._session_scope = session_scope

    async def save(self, cp: Checkpoint) -> None:
        from sqlalchemy import text
        sql = text(f"""
            INSERT INTO {self.TABLE}
              (qa_id, session_id, answer_checkpoint, sentinel_position, json_state,
               status, degrade_level, "offset", last_event_id, concept_ids,
               call_id, endpoint, raw_json, updated_at)
            VALUES (:qa_id,:sid,:ans,:sp,:js,:st,:dl,:off,:lei,:ci,:cid,:ep,:rj, now())
            ON CONFLICT (qa_id) DO UPDATE SET
              answer_checkpoint=EXCLUDED.answer_checkpoint,
              sentinel_position=EXCLUDED.sentinel_position,
              json_state=EXCLUDED.json_state, status=EXCLUDED.status,
              degrade_level=EXCLUDED.degrade_level, "offset"=EXCLUDED."offset",
              last_event_id=EXCLUDED.last_event_id, concept_ids=EXCLUDED.concept_ids,
              call_id=EXCLUDED.call_id, endpoint=EXCLUDED.endpoint,
              raw_json=EXCLUDED.raw_json, updated_at=now()
        """)
        async with self._session_scope() as s:
            await s.execute(sql.bindparams(
                qa_id=cp.qa_id, sid=cp.session_id, ans=cp.answer_checkpoint,
                sp=cp.sentinel_position, js=cp.json_state, st=cp.status,
                dl=cp.degrade_level, off=cp.offset, lei=cp.last_event_id,
                ci=json.dumps(cp.concept_ids), cid=cp.call_id, ep=cp.endpoint,
                rj=cp.raw_json,
            ))

    async def load(self, qa_id: str) -> Optional[Checkpoint]:
        from sqlalchemy import text
        async with self._session_scope() as s:
            row = (await s.execute(
                text(f"SELECT * FROM {self.TABLE} WHERE qa_id=:id").bindparams(id=qa_id)
            )).first()
        if row is None:
            return None
        m = row._mapping
        return Checkpoint(
            session_id=m["session_id"], qa_id=m["qa_id"],
            answer_checkpoint=m["answer_checkpoint"] or "",
            sentinel_position=m["sentinel_position"],
            json_state=m["json_state"], status=m["status"],
            degrade_level=m["degrade_level"], offset=m["offset"],
            last_event_id=m["last_event_id"],
            concept_ids=m["concept_ids"] or [],
            call_id=m["call_id"], endpoint=m["endpoint"],
            raw_json=m["raw_json"] or "",
        )

    async def delete(self, qa_id: str) -> None:
        from sqlalchemy import text
        async with self._session_scope() as s:
            await s.execute(
                text(f"DELETE FROM {self.TABLE} WHERE qa_id=:id").bindparams(id=qa_id)
            )
