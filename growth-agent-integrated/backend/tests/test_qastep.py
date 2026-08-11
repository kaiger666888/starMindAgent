"""纯逻辑测试：QAStep 状态机迁移合法性、StreamSplitter sentinel 检测。

不依赖数据库，可离线运行：pytest backend/tests/test_qastep.py
"""
import json
import pytest
from app.qastep.state_machine import (
    QAStatus, QAStepRuntime, IllegalTransition, QATransition,
)
from app.inference.protocol import StreamSplitter, SENTINEL
from app.schemas import ConceptBlock, ConceptItem


def test_legal_transitions():
    rt = QAStepRuntime("qa1", "s1", "q")
    # generating -> extracting -> waiting 合法
    rt.assert_transition(QAStatus.GENERATING, QAStatus.EXTRACTING)
    rt.assert_transition(QAStatus.EXTRACTING, QAStatus.WAITING)
    # 三个出口从 waiting 出发
    rt.assert_transition(QAStatus.WAITING, QAStatus.GENERATING)


def test_illegal_transition():
    rt = QAStepRuntime("qa1", "s1", "q")
    # waiting -> extracting 非法（只能 extracting -> waiting）
    with pytest.raises(IllegalTransition):
        rt.assert_transition(QAStatus.WAITING, QAStatus.EXTRACTING)
    # generating -> waiting 非法（必须经 extracting）
    with pytest.raises(IllegalTransition):
        rt.assert_transition(QAStatus.GENERATING, QAStatus.WAITING)


def test_prompt_hash_stable():
    h1 = QAStepRuntime.prompt_hash("什么是梯度下降", ["root"])
    h2 = QAStepRuntime.prompt_hash("什么是梯度下降", ["root"])
    h3 = QAStepRuntime.prompt_hash("什么是梯度下降", ["root", "conv"])
    assert h1 == h2
    assert h1 != h3  # 父链不同则 hash 不同


def test_stream_splitter_sentinel_split():
    sp = StreamSplitter()
    block = ConceptBlock(concepts=[ConceptItem(name="梯度下降", confidence=0.9)], model="m")
    raw = f"梯度下降是一种优化算法。\n它通过沿梯度反方向更新参数。\n{SENTINEL}\n{block.model_dump_json()}"
    # 模拟逐 chunk 喂入
    answers, saw_sentinel, parsed = [], False, None
    step = 7
    for i in range(0, len(raw), step):
        a, sent, blk = sp.feed(raw[i:i + step])
        if a:
            answers.append(a)
        if sent:
            saw_sentinel = True
        if blk is not None:
            parsed = blk
    # flush 残留
    a, _, _ = sp.flush()
    if a:
        answers.append(a)
    assert saw_sentinel
    assert parsed is not None
    assert parsed.concepts[0].name == "梯度下降"
    assert "".join(answers).startswith("梯度下降是一种优化算法。")
    # 正文不应包含 sentinel
    assert SENTINEL not in "".join(answers)


def test_stream_splitter_no_sentinel_degrades():
    """L1：无 sentinel / JSON 解析失败 -> 正文仍完整吐出。"""
    sp = StreamSplitter()
    raw = "只有正文没有结构化部分。"
    a, sent, blk = sp.feed(raw)
    a2, _, _ = sp.flush()
    assert not sent
    assert blk is None
    assert (a + a2) == raw
