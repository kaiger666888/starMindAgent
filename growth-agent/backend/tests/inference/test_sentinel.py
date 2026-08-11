"""Sentinel 检测器测试：跨 chunk 切断、正文不泄漏 sentinel、JSON 解析。"""
import pytest
from app.inference.sentinel import SentinelDetector, split_stream, SENTINEL
from app.inference.protocol import StreamSplitter  # 既有实现对照
from app.schemas import ConceptBlock, ConceptItem


def _block():
    return ConceptBlock(concepts=[ConceptItem(name="梯度下降", confidence=0.9)], model="m")


@pytest.mark.parametrize("step", [1, 2, 3, 5, 7, 11, 13])
def test_sentinel_cross_chunk_any_position(step):
    """sentinel 在 chunk 边界任意位置切断都能正确检测，正文不泄漏 sentinel。"""
    block = _block()
    raw = (f"梯度下降是一种优化算法。\n它沿梯度反方向更新参数。\n"
           f"{SENTINEL}\n{block.model_dump_json()}")
    det = SentinelDetector()
    answers, saw, parsed = [], False, None
    for i in range(0, len(raw), step):
        a, sent, blk = det.feed(raw[i:i + step])
        if a:
            answers.append(a)
        if sent:
            saw = True
        if blk is not None:
            parsed = blk
    a, _, _ = det.flush()
    if a:
        answers.append(a)
    assert saw, f"step={step} 未检测到 sentinel"
    assert parsed is not None and parsed.concepts[0].name == "梯度下降"
    joined = "".join(answers)
    assert SENTINEL not in joined, f"step={step} 正文泄漏 sentinel"
    assert joined.startswith("梯度下降是一种优化算法。")


def test_no_sentinel_degrades_full_prose():
    """L1：无 sentinel -> 正文仍完整吐出，无 block。"""
    det = SentinelDetector()
    raw = "只有正文没有结构化部分。"
    a, sent, blk = det.feed(raw)
    a2, _, _ = det.flush()
    assert not sent and blk is None
    assert (a + a2) == raw


def test_split_stream_oneshot():
    block = _block()
    raw = f"正文内容。\n{SENTINEL}\n{block.model_dump_json()}"
    answer, saw, parsed = split_stream(raw)
    assert saw and parsed is not None
    assert SENTINEL not in answer
    assert answer.startswith("正文内容。")


def test_protocol_splitter_still_works():
    """既有 StreamSplitter（被 11 基线测试引用）行为不变。"""
    sp = StreamSplitter()
    raw = f"正文。\n{SENTINEL}\n" + _block().model_dump_json()
    parts, saw, parsed = [], False, None
    for i in range(0, len(raw), 4):
        a, sent, blk = sp.feed(raw[i:i + 4])
        if a:
            parts.append(a)
        saw = saw or sent
        if blk is not None:
            parsed = blk
    a, _, _ = sp.flush()
    if a:
        parts.append(a)
    assert saw and parsed is not None
    assert SENTINEL not in "".join(parts)
