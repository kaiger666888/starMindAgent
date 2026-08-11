"""InferenceClient L0-L3 降级链路测试（协议第五节）。

用 FakeLLMBackend 模拟模型输出，覆盖：
- L0：单次调用成功（正文 + sentinel + JSON）；
- L0 via retry：JSON 解析失败后重试成功；
- L1：JSON 解析失败 + 重试失败 → error(L1) + backfill；
- L1：整体超时（正文已流出）；
- L2：拆两次调用，第二次成功；
- L3：L2 第二次失败 → 关键词匹配命中 / 未命中；
- 事件序列对齐 InferenceSession Protocol。
"""
import asyncio
import json
import pytest
from app.inference.client import InferenceClient
from app.inference.backend import FakeLLMBackend
from app.inference.context import ContextBudget, LayerSummary
from app.inference.constraints import ConstrainedDecoder
from app.config import settings

CORE = settings.concept_sentinel
SENT = f"\n{CORE}\n"


def _block_json(name="梯度下降", **ov):
    d = {
        "concepts": [{"name": name, "aliases": ["GD"], "confidence": 0.9, "relation_type": "related"}],
        "model": "fake",
    }
    d.update(ov)
    return json.dumps(d, ensure_ascii=False)


async def _collect(client):
    events = []
    async for ev in client.stream():
        events.append(ev)
    return events


def _kinds(events):
    return [e["kind"] for e in events]


# ----------------------------------------------------------------------- L0
@pytest.mark.asyncio
async def test_l0_single_call_success():
    body = "梯度下降是一种优化算法。"
    raw = [body, SENT, _block_json()]
    backend = FakeLLMBackend(chunks=raw)
    client = InferenceClient("qa1", "s1", "什么是梯度下降", backend=backend)
    events = await _collect(client)
    kinds = _kinds(events)
    assert kinds[0] == "delta"
    assert "sentinel" in kinds
    assert kinds[-1] == "json_done"
    block = events[-1]["block"]
    assert block.concepts[0].name == "梯度下降"
    assert block.prompt_hash  # 埋点回填
    # 正文拼接完整
    deltas = "".join(e["text"] for e in events if e["kind"] == "delta")
    assert deltas == body


@pytest.mark.asyncio
async def test_l0_cross_chunk_sentinel():
    """整个输出按小 chunk 切碎，sentinel 跨 chunk 仍正确检测。"""
    body = "反向传播通过链式法则计算梯度。"
    raw = body + SENT + _block_json("BP")
    # 切成 3 字符一段
    chunks = [raw[i:i+3] for i in range(0, len(raw), 3)]
    backend = FakeLLMBackend(chunks=chunks)
    client = InferenceClient("qa1", "s1", "q", backend=backend)
    events = await _collect(client)
    assert events[-1]["kind"] == "json_done"
    assert events[-1]["block"].concepts[0].name == "BP"
    deltas = "".join(e["text"] for e in events if e["kind"] == "delta")
    assert deltas == body
    assert CORE not in deltas


# ----------------------------------------------------------------- L0 retry
@pytest.mark.asyncio
async def test_l0_via_retry_after_parse_fail():
    """主调用 JSON 损坏，重试调用返回合法 JSON → L0。"""
    body = "正文"
    bad_json = SENT + "{broken json"
    backend = FakeLLMBackend(
        chunks=[body, bad_json],
        complete_text=_block_json(),
    )
    client = InferenceClient("qa1", "s1", "q", backend=backend, constrained=ConstrainedDecoder(False))
    events = await _collect(client)
    assert events[-1]["kind"] == "json_done"
    assert events[-1]["block"].concepts[0].name == "梯度下降"
    assert backend.call_count == 1  # 触发了一次重试


# ----------------------------------------------------------------------- L1
@pytest.mark.asyncio
async def test_l1_json_fail_and_retry_fail():
    body = "正文内容"
    backend = FakeLLMBackend(
        chunks=[body, SENT, "{still broken"],
        complete_text="{also broken",
    )
    backfilled = []
    client = InferenceClient(
        "qa1", "s1", "q", backend=backend,
        constrained=ConstrainedDecoder(False),
        backfill_hook=lambda qid: asyncio.sleep(0, result=backfilled.append(qid)),
    )
    events = await _collect(client)
    kinds = _kinds(events)
    assert "sentinel" in kinds
    assert kinds[-1] == "error"
    err = events[-1]
    assert err["level"] == "L1"
    assert err["needs_backfill"] is True
    assert backfilled == ["qa1"]
    # 正文完整
    deltas = "".join(e["text"] for e in events if e["kind"] == "delta")
    assert deltas == body


@pytest.mark.asyncio
async def test_l1_no_sentinel_synthetic_sentinel_then_error():
    """模型完全不产 sentinel：合成 sentinel 后 L1。"""
    backend = FakeLLMBackend(chunks=["只有正文，没有结构化。"], complete_text="{bad")
    client = InferenceClient(
        "qa1", "s1", "q", backend=backend,
        constrained=ConstrainedDecoder(False),
        backfill_hook=lambda qid: asyncio.sleep(0),
    )
    events = await _collect(client)
    kinds = _kinds(events)
    assert "delta" in kinds
    assert "sentinel" in kinds  # 合成的
    assert kinds[-1] == "error"
    assert events[-1]["level"] == "L1"


@pytest.mark.asyncio
async def test_l1_overall_timeout_after_text_streamed():
    """整体超时：正文已流出 → L1，不重试。"""
    body = "部分正文"
    # 用一个永远不结束的 stream 模拟超时：通过极小的 overall_timeout
    async def slow_stream(*a, **kw):
        yield body
        await asyncio.sleep(10)
    backend = FakeLLMBackend()
    backend._chunks = None
    # 直接替换 stream 方法
    backend.stream = slow_stream
    client = InferenceClient(
        "qa1", "s1", "q", backend=backend,
        constrained=ConstrainedDecoder(False),
        overall_timeout=0.3,
        first_token_timeout=5,
        backfill_hook=lambda qid: asyncio.sleep(0),
    )
    events = await _collect(client)
    kinds = _kinds(events)
    assert "delta" in kinds
    assert "sentinel" in kinds
    assert kinds[-1] == "error"
    assert events[-1]["level"] == "L1"


# ----------------------------------------------------------------------- L2
@pytest.mark.asyncio
async def test_l2_two_call_success():
    """模型不支持并存：第一次流式回答，第二次抽取成功。"""
    body = "卷积神经网络通过卷积层提取特征。"
    backend = FakeLLMBackend(
        chunks=[body[i:i+4] for i in range(0, len(body), 4)],
        complete_text=_block_json("CNN"),
    )
    client = InferenceClient(
        "qa1", "s1", "q", backend=backend,
        co_streaming_supported=False,
        concept_table=[{"canonical_name": "CNN", "aliases": ["卷积"]}],
    )
    events = await _collect(client)
    kinds = _kinds(events)
    # L2：delta* / sentinel / json_done
    assert "sentinel" in kinds
    assert kinds[-1] == "json_done"
    assert events[-1]["block"].concepts[0].name == "CNN"
    deltas = "".join(e["text"] for e in events if e["kind"] == "delta")
    assert deltas == body


# ----------------------------------------------------------------------- L3
@pytest.mark.asyncio
async def test_l3_keyword_fallback_hit():
    """L2 第二次抽取失败 → 关键词匹配命中。"""
    body = "梯度下降和反向传播是关键。"
    backend = FakeLLMBackend(
        chunks=[body],
        complete_text="{broken",  # 抽取调用失败
    )
    client = InferenceClient(
        "qa1", "s1", "q", backend=backend,
        co_streaming_supported=False,
        concept_table=[
            {"canonical_name": "梯度下降", "aliases": ["GD"]},
            {"canonical_name": "反向传播", "aliases": ["BP"]},
        ],
    )
    events = await _collect(client)
    assert events[-1]["kind"] == "json_done"
    names = [c.name for c in events[-1]["block"].concepts]
    assert "梯度下降" in names
    assert "反向传播" in names


@pytest.mark.asyncio
async def test_l3_keyword_fallback_miss():
    """L2 抽取失败 + 关键词也无命中 → error(L3)。"""
    body = "完全不相关的内容。"
    backend = FakeLLMBackend(chunks=[body], complete_text="{bad")
    client = InferenceClient(
        "qa1", "s1", "q", backend=backend,
        co_streaming_supported=False,
        concept_table=[{"canonical_name": "梯度下降", "aliases": ["GD"]}],
        backfill_hook=lambda qid: asyncio.sleep(0),
    )
    events = await _collect(client)
    assert events[-1]["kind"] == "error"
    assert events[-1]["level"] == "L3"
    assert events[-1]["needs_backfill"] is True


# -------------------------------------------------------- Protocol alignment
@pytest.mark.asyncio
async def test_protocol_attributes_present():
    backend = FakeLLMBackend(chunks=["x", SENT, _block_json()])
    client = InferenceClient("qa7", "s7", "q", backend=backend)
    # InferenceSession Protocol 要求 session_id / qa_id
    assert client.session_id == "s7"
    assert client.qa_id == "qa7"
    assert hasattr(client, "stream")


@pytest.mark.asyncio
async def test_event_kinds_are_valid_protocol_kinds():
    backend = FakeLLMBackend(chunks=["x", SENT, _block_json()])
    client = InferenceClient("qa", "s", "q", backend=backend)
    events = await _collect(client)
    valid = {"delta", "sentinel", "json_done", "error"}
    for e in events:
        assert e["kind"] in valid
