"""Harness checkpoint 恢复测试：save / resume / 未知 qa clean failure。"""
import asyncio
import pytest
from app.harness.models import Checkpoint, JsonState, SessionStatus
from app.harness.store import InMemoryCheckpointStore
from app.harness.recovery import RecoveryManager


@pytest.mark.asyncio
async def test_checkpoint_save_and_resume():
    store = InMemoryCheckpointStore()
    rm = RecoveryManager(store)
    cp = Checkpoint(qa_id="qa1", session_id="s1", answer_checkpoint="部分正文",
                    sentinel_position=4, json_state=JsonState.ACCUMULATING,
                    model="m")
    await rm.save_checkpoint(cp)
    res = await rm.resume("qa1")
    assert res.status == "resumed"
    assert res.checkpoint.answer_checkpoint == "部分正文"
    assert res.checkpoint.sentinel_position == 4


@pytest.mark.asyncio
async def test_resume_unknown_qa_clean_failure():
    """resume 未知 qa 返回 status=unknown（统一结构，recovery 层判 clean failure）。"""
    store = InMemoryCheckpointStore()
    rm = RecoveryManager(store)
    res = await rm.resume("nonexistent")
    assert res.status == "unknown"
    assert res.checkpoint is None


@pytest.mark.asyncio
async def test_user_back_marks_interrupted_keeps_prose():
    """用户回上层：状态置 interrupted，正文落盘保留。"""
    store = InMemoryCheckpointStore()
    rm = RecoveryManager(store)
    cp = Checkpoint(qa_id="qa2", session_id="s1", answer_checkpoint="正文")
    await rm.save_checkpoint(cp)
    out = await rm.on_user_back("qa2")
    assert out.status == SessionStatus.INTERRUPTED
    assert out.answer_checkpoint == "正文"  # 正文保留
