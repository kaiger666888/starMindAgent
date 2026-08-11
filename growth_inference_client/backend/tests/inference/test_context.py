"""ContextBudget 上下文膨胀控制测试（协议第六节）。

覆盖：
- 正常构造 messages，总 token ≤ 2K；
- 深层概念链超限触发逐层压缩；
- 仅 parent_chain 时退化为列表；
- 抽取调用 messages 构造。
"""
import pytest
from app.inference.context import (
    ContextBudget, LayerSummary, estimate_tokens, TOTAL_BUDGET,
)


def test_basic_messages_within_budget():
    cb = ContextBudget()
    msgs = cb.build_messages(
        question="什么是反向传播",
        parent_chain=["神经网络"],
        layer_summaries=[LayerSummary(canonical_name="神经网络", one_liner="由神经元组成的网络")],
    )
    assert msgs[0]["role"] == "system"
    assert "反向传播" in msgs[1]["content"]
    total = sum(estimate_tokens(m["content"]) for m in msgs)
    assert total <= TOTAL_BUDGET


def test_deep_chain_compressed():
    """构造超长概念链，验证压缩后不超 chain_budget。"""
    cb = ContextBudget()
    summaries = [
        LayerSummary(
            canonical_name=f"概念{i}",
            one_liner="这是一个很长的定位说明" * 30,
            sibling_concepts=[f"兄弟{j}" for j in range(10)],
        )
        for i in range(8)
    ]
    msgs = cb.build_messages(
        question="深层问题", parent_chain=[s.canonical_name for s in summaries],
        layer_summaries=summaries,
    )
    total = sum(estimate_tokens(m["content"]) for m in msgs)
    assert total <= TOTAL_BUDGET


def test_chain_drops_non_current_branch():
    """压缩到最后只保留当前分支主干。"""
    cb = ContextBudget()
    summaries = [
        LayerSummary(canonical_name="主干A", one_liner="x" * 200, is_current_branch=True),
        LayerSummary(canonical_name="兄弟B", one_liner="y" * 200, is_current_branch=False),
        LayerSummary(canonical_name="兄弟C", one_liner="z" * 200, is_current_branch=False),
    ]
    # 人为把 chain_budget 压到很小，强制走第三遍压缩（丢弃非当前分支兄弟）
    cb.chain_budget = 8
    msgs = cb.build_messages(
        question="q", parent_chain=[s.canonical_name for s in summaries],
        layer_summaries=summaries,
    )
    content = msgs[1]["content"]
    assert "主干" in content
    # 非当前分支兄弟应被丢弃
    assert "兄弟B" not in content


def test_parent_chain_only():
    cb = ContextBudget()
    msgs = cb.build_messages(question="问题", parent_chain=["A", "B", "C"])
    assert "A" in msgs[1]["content"]
    assert "B" in msgs[1]["content"]


def test_extract_messages():
    cb = ContextBudget()
    msgs = cb.build_extract_messages("回答正文", "原始问题", parent_chain=["父概念"])
    assert msgs[0]["role"] == "system"
    assert "回答正文" in msgs[1]["content"]
    assert "原始问题" in msgs[1]["content"]
    assert "父概念" in msgs[1]["content"]


def test_estimate_tokens_positive():
    assert estimate_tokens("") == 0
    assert estimate_tokens("hello world") > 0
    assert estimate_tokens("这是一段中文") > 0
