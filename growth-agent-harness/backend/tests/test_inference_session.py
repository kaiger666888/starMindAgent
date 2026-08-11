"""InferenceSession 生产封装：L0/L1/超时/重试/降级（设计规格 §二 §四 / 协议 §5）。

覆盖路径：
- L0 正常：delta + sentinel + json_done
- L1 JSON 失败 -> 结构化重试(extract_only)恢复 -> json_done
- L1 JSON 失败 + 结构化重试失败 -> error(L1) + 补标注入队
- L1 无 sentinel：正文完整无结构化 -> 补标注入队（不报 error）
- 首 token 超时重试一次成功 / 重试仍失败 -> L1
- 整体超时：正文已开始 -> 只降级不重试
- 正文已渲染后流异常 -> 只降级不重试
- abort：用户回上层 -> 落盘保留现场
"""
import asyncio

import pytest

from app.harness.inference_client import StubInferenceClient, _Script
from app.harness.models import InferenceRequest
from app.schemas import ConceptBlock, ConceptItem
from tests.conftest import make_manager, make_script


class FakeQueue:
    def __init__(self):
        self.enqueued = []

    async def enqueue(self, qa_id, session_id, answer_snapshot, reason="L1"):
        self.enqueued.append({"qa_id": qa_id, "session_id": session_id,
                              "answer_snapshot": answer_snapshot, "reason": reason})


class FirstTokenTimeoutThenSuccess:
    """第 1 次流首 token 超时，第 2 次（重试）正常。"""
    def __init__(self, block, first_token_s):
        self.block = block
        self.first_token_s = first_token_s
        self.calls = 0

    async def stream(self, req):
        self.calls += 1
        call_id = f"c{self.calls}"
        if self.calls == 1:
            await asyncio.sleep(self.first_token_s + 0.2)  # 超时
            yield {"delta": "never"}  # 不会到达
            return
        from app.harness.models import StreamChunk, SENTINEL
        yield StreamChunk(call_id=call_id, delta="重试后正文")
        yield StreamChunk(call_id=call_id, delta=SENTINEL)
        yield StreamChunk(call_id=call_id, delta=self.block.model_dump_json(), finish_reason="stop")

    async def abort(self, call_id):
        pass

    async def extract_only(self, text, model=None):
        return self.block


class SlowSecondChunk:
    """首 token 立即到，第二块超过整体超时 -> 整体超时降级。"""
    def __init__(self, overall_s):
        self.overall_s = overall_s

    async def stream(self, req):
        from app.harness.models import StreamChunk
        call_id = "c1"
        yield StreamChunk(call_id=call_id, delta="首块正文")  # body_started=True
        await asyncio.sleep(self.overall_s + 0.3)  # 触发整体超时
        yield StreamChunk(call_id=call_id, delta="不会到达")

    async def abort(self, call_id):
        pass

    async def extract_only(self, text, model=None):
        raise RuntimeError("nope")


class StreamErrorAfterFirstDelta:
    """首 delta 后 stream 抛异常 -> 正文已渲染只降级不重试。"""
    async def stream(self, req):
        from app.harness.models import StreamChunk
        yield StreamChunk(call_id="c1", delta="部分正文")
        raise RuntimeError("stream blew up")

    async def abort(self, call_id):
        pass

    async def extract_only(self, text, model=None):
        raise RuntimeError("nope")


async def _drain(sess):
    events = []
    async for ev in sess.stream():
        events.append(ev)
    return events


@pytest.mark.asyncio
async def test_l0_happy_path(block):
    client = StubInferenceClient(default=make_script(answer_chunks=["关于X，", "核心在Y。"], concept_block=block))
    mgr = make_manager(client)
    sess = await mgr.start("s1", "q1", "prompt", endpoint="primary")
    events = await _drain(sess)
    kinds = [e["kind"] for e in events]
    assert "delta" in kinds
    assert "sentinel" in kinds
    assert kinds[-1] == "json_done"
    assert events[-1]["block"].concepts[0].name == "梯度下降"
    st = sess.state()
    assert st["degrade_level"] == "L0"
    assert st["status"] == "completed"
    assert st["offset"] > 0


@pytest.mark.asyncio
async def test_l1_json_fail_structured_retry_recovers(block):
    client = StubInferenceClient(
        scripts={"primary": make_script(json_fail=True)},
        default=make_script(extract_block=block),
    )
    mgr = make_manager(client)
    sess = await mgr.start("s1", "q1", "prompt", endpoint="primary")
    events = await _drain(sess)
    assert events[-1]["kind"] == "json_done"
    assert sess.state()["degrade_level"] == "L0"


@pytest.mark.asyncio
async def test_l1_json_fail_and_extract_fail_enqueues_reannotation(block):
    q = FakeQueue()
    client = StubInferenceClient(
        scripts={"primary": make_script(json_fail=True)},
        default=make_script(extract_fail=True),
    )
    mgr = make_manager(client, reannotation_queue=q)
    sess = await mgr.start("s1", "q1", "prompt", endpoint="primary")
    events = await _drain(sess)
    assert events[-1]["kind"] == "error"
    assert events[-1]["degrade"] == "L1"
    assert sess.state()["degrade_level"] == "L1"
    assert len(q.enqueued) == 1
    assert q.enqueued[0]["qa_id"] == "q1"


@pytest.mark.asyncio
async def test_l1_no_sentinel_body_complete_no_concepts(block):
    q = FakeQueue()
    client = StubInferenceClient(default=make_script(answer_chunks=["完整正文一段。"], no_sentinel=True))
    mgr = make_manager(client, reannotation_queue=q)
    sess = await mgr.start("s1", "q1", "prompt", endpoint="primary")
    events = await _drain(sess)
    kinds = [e["kind"] for e in events]
    assert kinds == ["delta"]  # 无 sentinel / json_done / error
    assert sess.state()["degrade_level"] == "L1"
    assert sess.state()["status"] == "completed"
    assert len(q.enqueued) == 1


@pytest.mark.asyncio
async def test_first_token_timeout_retry_success(block, fast_timeouts):
    client = FirstTokenTimeoutThenSuccess(block, fast_timeouts.first_token_s)
    mgr = make_manager(client, timeouts=fast_timeouts)
    sess = await mgr.start("s1", "q1", "prompt", endpoint="primary")
    events = await _drain(sess)
    assert events[-1]["kind"] == "json_done"
    assert client.calls == 2  # 重试一次
    assert sess.state()["degrade_level"] == "L0"


@pytest.mark.asyncio
async def test_first_token_timeout_retry_fail_degrades(fast_timeouts):
    # 两次都首 token 超时
    class AlwaysTimeout(FirstTokenTimeoutThenSuccess):
        async def stream(self, req):
            self.calls += 1
            await asyncio.sleep(self.first_token_s + 0.2)
            yield {"delta": "never"}
            return
    q = FakeQueue()
    client = AlwaysTimeout(None, fast_timeouts.first_token_s)
    mgr = make_manager(client, timeouts=fast_timeouts, reannotation_queue=q)
    sess = await mgr.start("s1", "q1", "prompt", endpoint="primary")
    events = await _drain(sess)
    assert events[-1]["kind"] == "error"
    assert events[-1]["degrade"] == "L1"
    assert client.calls == 2  # 重试一次后放弃
    assert sess.state()["degrade_level"] == "L1"
    assert len(q.enqueued) == 1


@pytest.mark.asyncio
async def test_overall_timeout_body_started_only_degrade(fast_timeouts):
    q = FakeQueue()
    client = SlowSecondChunk(fast_timeouts.overall_s)
    mgr = make_manager(client, timeouts=fast_timeouts, reannotation_queue=q)
    sess = await mgr.start("s1", "q1", "prompt", endpoint="primary")
    events = await _drain(sess)
    assert events[-1]["kind"] == "error"
    assert events[-1]["degrade"] == "L1"
    # 正文已开始 -> 不重试（只有一次 delta）
    assert sum(1 for e in events if e["kind"] == "delta") == 1
    assert sess.state()["degrade_level"] == "L1"
    assert len(q.enqueued) == 1


@pytest.mark.asyncio
async def test_stream_error_after_body_started_no_retry(fast_timeouts):
    q = FakeQueue()
    client = StreamErrorAfterFirstDelta()
    mgr = make_manager(client, timeouts=fast_timeouts, reannotation_queue=q)
    sess = await mgr.start("s1", "q1", "prompt", endpoint="primary")
    events = await _drain(sess)
    # 首 delta 已渲染 -> 异常只降级，不重试整次调用
    assert any(e["kind"] == "delta" for e in events)
    assert events[-1]["kind"] == "error"
    assert events[-1]["degrade"] == "L1"
    assert sess.state()["degrade_level"] == "L1"
    assert len(q.enqueued) == 1


@pytest.mark.asyncio
async def test_abort_preserves_body_checkpoint(block):
    client = StubInferenceClient(default=make_script(answer_chunks=["第一段，", "第二段。"], concept_block=block))
    mgr = make_manager(client)
    sess = await mgr.start("s1", "q1", "prompt", endpoint="primary")
    # 只消费第一个 delta 后 abort
    it = sess.stream().__aiter__()
    first = await it.__anext__()
    assert first["kind"] == "delta"
    snap = await sess.abort()
    assert snap["status"] == "interrupted"
    assert snap["offset"] > 0  # 已流出正文落盘保留
    assert "第一段" in snap["answer_checkpoint"]


@pytest.mark.asyncio
async def test_circuit_failover_to_backup(block, fast_timeouts, fast_breaker_config):
    # primary 流异常，连续失败切 backup；backup 正常
    from app.harness.circuit_breaker import CircuitBreakerRegistry, ResilientCaller, RetryPolicy
    from app.harness.manager import InferenceSessionManager
    from app.harness.store import InMemoryCheckpointStore
    reg = CircuitBreakerRegistry(fast_breaker_config)
    caller = ResilientCaller(reg, failover_map={"primary": ["backup"]})
    client = StubInferenceClient(
        scripts={"primary": make_script(raise_on_stream=RuntimeError("primary down")),
                 "backup": make_script(answer_chunks=["备用端正文"], concept_block=block)},
    )
    mgr = InferenceSessionManager(client=client, caller=caller, retry=RetryPolicy(),
                                  timeouts=fast_timeouts, checkpoint_store=InMemoryCheckpointStore())
    # 第一次：primary 异常 -> 首 token 阶段异常 -> 重试一次仍 primary（未 OPEN）-> L1
    sess = await mgr.start("s1", "q1", "prompt", endpoint="primary")
    events = await _drain(sess)
    assert events[-1]["kind"] == "error"
    # primary 已失败若干次；再触发使 OPEN，下一次走 backup
    for _ in range(3):
        sess2 = await mgr.start("s1", "q2", "prompt", endpoint="primary")
        ev2 = await _drain(sess2)
        if ev2[-1]["kind"] == "json_done":
            assert ev2[-1]["block"].concepts[0].name == "梯度下降"
            assert reg.get("backup").state.value in ("closed", "half_open")
            return
    pytest.fail("should have failed over to backup and succeeded")
