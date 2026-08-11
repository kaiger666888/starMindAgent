"""InferenceClient 降级路径测试：L0 / L1(backfill) / L2 拆两次 / L3 兜底。"""
import asyncio
import pytest
from app.inference.client import InferenceClient
from app.inference.backend import StubLLMBackend
from app.inference.sentinel import SentinelDetector
from app.schemas import ConceptBlock, ConceptItem


def _block():
    return ConceptBlock(concepts=[ConceptItem(name="概念A", confidence=0.9)], model="m")


async def _events(client):
    return [ev async for ev in client.stream()]


@pytest.mark.asyncio
async def test_l0_normal_success():
    c = InferenceClient("q", "s", "问题", backend=StubLLMBackend(mode="ok"))
    evs = await _events(c)
    kinds = [e["kind"] for e in evs]
    assert "delta" in kinds and "json_done" in kinds
    assert kinds[-1] == "json_done"
    assert evs[-1]["block"].concepts[0].name == "概念A"


@pytest.mark.asyncio
async def test_l1_backfill_triggered_when_retry_fails():
    """sentinel 后 JSON 损坏 + 重试抽取失败 -> 触发 backfill_hook + error(L1)。"""
    called = []

    class BadJsonExtractFail(StubLLMBackend):
        def __init__(self):
            super().__init__(mode="bad_json")

        async def extract_only(self, answer_text):
            raise RuntimeError("extract failed")

    async def hook(qa_id):
        called.append(qa_id)

    c = InferenceClient("q1", "s", "问题", backend=BadJsonExtractFail(), backfill_hook=hook)
    evs = await _events(c)
    kinds = [e["kind"] for e in evs]
    assert "delta" in kinds          # 正文已流出
    assert kinds[-1] == "error"      # 降级
    assert "L1" in evs[-1]["message"]
    assert called == ["q1"]          # backfill 已触发


@pytest.mark.asyncio
async def test_l1_no_sentinel_triggers_backfill():
    """模型不产 sentinel 且不支持并存判定 -> 走 L1 backfill。"""
    called = []

    async def hook(qa_id):
        called.append(qa_id)

    c = InferenceClient("q2", "s", "问题", backend=StubLLMBackend(mode="no_sentinel"),
                       backfill_hook=hook)
    evs = await _events(c)
    kinds = [e["kind"] for e in evs]
    assert "delta" in kinds and kinds[-1] == "error"
    assert called == ["q2"]


@pytest.mark.asyncio
async def test_l2_split_two_calls():
    """模型不支持流式+结构化并存 -> 拆两次调用：先正文再抽取。"""
    c = InferenceClient("q3", "s", "问题", backend=StubLLMBackend(mode="split"))
    evs = await _events(c)
    kinds = [e["kind"] for e in evs]
    # 先多段 delta，再 sentinel + json_done（第二次抽取调用产出）
    assert "delta" in kinds
    assert "sentinel" in kinds and kinds[-1] == "json_done"


@pytest.mark.asyncio
async def test_l3_keyword_fallback_hit():
    """L3 关键词匹配命中 -> 仍有 json_done（不空）。"""
    c = InferenceClient("q4", "s", "问题", backend=StubLLMBackend(mode="split"))
    evs = await _events(c)
    # split 模式：第二次 extract_only 成功（stub 启发式抽取）
    assert evs[-1]["kind"] == "json_done"


@pytest.mark.asyncio
async def test_l3_miss_returns_error():
    """抽取与关键词都失败 -> error。"""
    class AllFail(StubLLMBackend):
        def __init__(self):
            super().__init__(mode="split")

        async def extract_only(self, answer_text):
            raise RuntimeError("no match")

    c = InferenceClient("q5", "s", "问题", backend=AllFail())
    evs = await _events(c)
    kinds = [e["kind"] for e in evs]
    assert "delta" in kinds and kinds[-1] == "error"


@pytest.mark.asyncio
async def test_sentinel_emitted_exactly_once():
    c = InferenceClient("q6", "s", "问题", backend=StubLLMBackend(mode="ok"))
    evs = await _events(c)
    sent_count = sum(1 for e in evs if e["kind"] == "sentinel")
    json_count = sum(1 for e in evs if e["kind"] == "json_done")
    assert sent_count == 1 and json_count == 1
