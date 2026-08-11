"""Sentinel 跨 chunk 检测 + JSON 累积解析（协议 §3.2 / §3.3 / §4.2）。"""
import json

from app.harness.models import SENTINEL
from app.harness.sentinel import (
    JsonAccumulator, SentinelDetector, _extract_json_block, _looks_complete,
)
from app.schemas import ConceptBlock, ConceptItem


def _block_json():
    b = ConceptBlock(concepts=[ConceptItem(name="梯度下降", confidence=0.9)], model="m")
    return b.model_dump_json()


def test_sentinel_split_across_chunks():
    raw = f"梯度下降是一种优化算法。\n它沿梯度反方向更新参数。\n{SENTINEL}\n{_block_json()}"
    det = SentinelDetector()
    answers, hit = [], False
    step = 5  # 故意把 sentinel 切到 chunk 边界
    for i in range(0, len(raw), step):
        text, h = det.feed(raw[i:i + step])
        if text:
            answers.append(text)
        if h:
            hit = True
    tail = det.drain_json()
    assert hit
    assert SENTINEL not in "".join(answers)
    assert "".join(answers).startswith("梯度下降是一种优化算法。")
    # 剩余片段可解析为 JSON
    acc = JsonAccumulator()
    acc.feed(tail)
    blk = acc.try_parse(strict=True)
    assert blk is not None
    assert blk.concepts[0].name == "梯度下降"


def test_no_sentinel_degrades_to_plain_body():
    det = SentinelDetector()
    raw = "只有正文，没有结构化部分。"
    out = []
    for i in range(0, len(raw), 3):
        t, h = det.feed(raw[i:i + 3])
        if t:
            out.append(t)
        assert not h
    out.append(det.flush_answer())
    assert "".join(out) == raw
    assert not det.hit


def test_json_accumulator_fenced_markdown():
    acc = JsonAccumulator()
    acc.feed("```json\n")
    acc.feed(_block_json())
    acc.feed("\n```")
    blk = acc.try_parse()
    assert blk is not None
    assert blk.concepts[0].name == "梯度下降"


def test_json_accumulator_partial_then_complete():
    acc = JsonAccumulator()
    acc.feed('{"concepts":[{"name":"x"')
    assert acc.try_parse() is None  # 未闭合
    acc.feed(',"confidence":0.5}],"model":"m"}')
    blk = acc.try_parse()
    assert blk is not None
    assert blk.concepts[0].name == "x"


def test_extract_json_block_outermost_braces():
    s = 'prefix {"a":{"b":1}} suffix'
    assert _extract_json_block(s) == '{"a":{"b":1}}'


def test_looks_complete_balanced():
    assert _looks_complete('{"a":1}')
    assert not _looks_complete('{"a":1')
    assert not _looks_complete('{"a":"}"}') is False  # 字符串内的 } 不算闭合 -> 实际 complete
    assert _looks_complete('{"a":"}"}')  # 字符串内的 } 不破坏配平
