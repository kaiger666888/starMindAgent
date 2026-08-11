"""Checkpoint 恢复 + 中断恢复协调器（设计规格 §三 / 协议 §7.2）。"""
import pytest

from app.harness.circuit_breaker import CircuitBreakerRegistry, ResilientCaller, RetryPolicy
from app.harness.manager import InferenceSessionManager
from app.harness.models import HarnessTimeouts
from app.harness.recovery import RecoveryCoordinator
from app.harness.store import InMemoryCheckpointStore
from app.harness.inference_client import StubInferenceClient
from tests.conftest import make_script


def _mgr(client, **kw):
    timeouts = HarnessTimeouts(first_token_s=0.3, overall_s=1.0, json_s=0.5)
    reg = CircuitBreakerRegistry()
    return InferenceSessionManager(
        client=client, caller=ResilientCaller(reg), retry=RetryPolicy(),
        timeouts=timeouts, checkpoint_store=InMemoryCheckpointStore(), **kw,
    )


@pytest.mark.asyncio
async def test_user_rollback_persists_and_resumes(block):
    client = StubInferenceClient(default=make_script(
        answer_chunks=["正文第一段。", "正文第二段。"], concept_block=block))
    mgr = _mgr(client)
    rec = RecoveryCoordinator(mgr)
    sess = await mgr.start("s1", "qa1", "p", endpoint="primary")

    it = sess.stream().__aiter__()
    await it.__anext__()  # 消费一个 delta
    snap = await rec.handle_user_rollback("qa1")
    assert snap["status"] == "interrupted"
    assert snap["offset"] > 0
    assert "正文" in snap["answer_checkpoint"]

    # 网络断开重连：从 checkpoint 续推，推理不重启
    res = await rec.handle_reconnect("qa1", last_event_id=0)
    cp = res["checkpoint"]
    assert cp["status"] == "interrupted"
    assert cp["offset"] == snap["offset"]
    assert res["resume_offset"] == snap["offset"]
    # 重放事件存在
    assert any(e["type"] == "answer_replay" for e in res["events"])
    assert rec.success_rate() == 1.0


@pytest.mark.asyncio
async def test_resume_unknown_qa_is_clean_failure(block):
    client = StubInferenceClient(default=make_script(concept_block=block))
    mgr = _mgr(client)
    rec = RecoveryCoordinator(mgr)
    res = await rec.handle_reconnect("nope", last_event_id=0)
    assert res["checkpoint"]["status"] == "unknown"
    # 一次失败 -> 成功率 0
    assert rec.success_rate() == 0.0


@pytest.mark.asyncio
async def test_completed_session_resume_replays_concepts(block):
    client = StubInferenceClient(default=make_script(
        answer_chunks=["正文。"], concept_block=block))
    mgr = _mgr(client)
    sess = await mgr.start("s1", "qa2", "p", endpoint="primary")
    async for _ in sess.stream():
        pass
    # 完成后断连重连：重放 answer + concepts
    res = await mgr.resume("qa2", last_event_id=0)
    types = [e["type"] for e in res["events"]]
    assert "answer_replay" in types
    assert "concepts_replay" in types
    assert res["checkpoint"]["json_state"] == "parsed"


@pytest.mark.asyncio
async def test_process_restart_rehydrates_from_persisted_checkpoint(block):
    """进程重启：live session 丢失，从持久化 checkpoint 重水化恢复中断现场。"""
    store = InMemoryCheckpointStore()
    client = StubInferenceClient(default=make_script(concept_block=block))
    mgr1 = _mgr(client)
    mgr1.checkpoint_store = store
    sess = await mgr1.start("s1", "qa3", "p", endpoint="primary")
    it = sess.stream().__aiter__()
    await it.__anext__()
    await sess.abort()

    # 新 manager（模拟重启），共享 checkpoint store，无 live session
    mgr2 = _mgr(client)
    mgr2.checkpoint_store = store
    snap = await mgr2.abort("qa3")  # 无 live -> 从持久化恢复
    assert snap["status"] == "interrupted"
    assert snap["offset"] > 0
    res = await mgr2.resume("qa3", last_event_id=0)
    assert res["checkpoint"]["offset"] > 0
