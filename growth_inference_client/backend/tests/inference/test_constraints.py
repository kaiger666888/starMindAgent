"""ConstrainedDecoder + 关键词降级测试（协议四、5.2）。

覆盖：
- 直接 JSON 解析（约束解码路径）；
- markdown 围栏剥离；
- 最外层花括号配对（JSON 外有说明文字）；
- 非法 JSON / 字段缺失 → None；
- L3 关键词匹配兜底。
"""
import json
import pytest
from app.inference.constraints import (
    ConstrainedDecoder, keyword_fallback, _extract_outermost_json, _strip_code_fence,
)
from app.schemas import ConceptBlock, ConceptItem


def _block_json(**overrides):
    data = {
        "concepts": [
            {"name": "梯度下降", "aliases": ["GD"], "confidence": 0.9, "relation_type": "related"}
        ],
        "model": "test-llm",
    }
    data.update(overrides)
    return json.dumps(data, ensure_ascii=False)


def test_extract_plain_json():
    dec = ConstrainedDecoder()
    block = dec.extract(_block_json())
    assert block is not None
    assert block.concepts[0].name == "梯度下降"
    assert block.concepts[0].aliases == ["GD"]


def test_extract_with_code_fence():
    dec = ConstrainedDecoder()
    raw = f"```json\n{_block_json()}\n```"
    block = dec.extract(raw)
    assert block is not None
    assert block.concepts[0].name == "梯度下降"


def test_extract_with_surrounding_text():
    """JSON 外有说明文字，靠最外层花括号配对定位。"""
    dec = ConstrainedDecoder()
    raw = f"好的，以下是抽取结果：\n{_block_json()}\n以上为概念块。"
    block = dec.extract(raw)
    assert block is not None
    assert block.concepts[0].name == "梯度下降"


def test_extract_empty_concepts_returns_none():
    dec = ConstrainedDecoder()
    block = dec.extract(json.dumps({"concepts": []}))
    assert block is None


def test_extract_missing_required_field_returns_none():
    dec = ConstrainedDecoder()
    # confidence 缺失但有默认值；name 缺失应失败
    block = dec.extract(json.dumps({"concepts": [{"aliases": []}]}))
    assert block is None


def test_extract_garbage_returns_none():
    dec = ConstrainedDecoder()
    assert dec.extract("这不是 JSON") is None
    assert dec.extract("") is None
    assert dec.extract(None) is None


def test_extract_unclosed_json_returns_none():
    dec = ConstrainedDecoder()
    assert dec.extract('{"concepts":[{"name":"x"') is None


def test_outermost_json_extraction_nested():
    text = 'prefix {"a":{"b":1},"c":[1,2]} suffix'
    assert _extract_outermost_json(text) == '{"a":{"b":1},"c":[1,2]}'


def test_outermost_json_with_brace_in_string():
    text = '{"k":"val}ue"}'
    assert _extract_outermost_json(text) == '{"k":"val}ue"}'


def test_strip_code_fence():
    assert _strip_code_fence("```json\n{x}\n```") == "{x}"
    assert _strip_code_fence("```\n{x}\n```") == "{x}"


def test_guided_params_when_supported():
    dec = ConstrainedDecoder(guided_supported=True)
    params = dec.guided_params()
    assert params is not None
    assert "guided_json" in params
    assert "response_format" in params


def test_guided_params_none_when_unsupported():
    dec = ConstrainedDecoder(guided_supported=False)
    # 即使 outlines 未装，guided_supported=False 时不应提供约束
    if not dec._native_constrained:
        assert dec.guided_params() is None


def test_keyword_fallback_hits():
    table = [
        {"canonical_name": "梯度下降", "aliases": ["GD", "gradient descent"]},
        {"canonical_name": "反向传播", "aliases": ["BP"]},
    ]
    answer = "梯度下降和反向传播是训练神经网络的关键。"
    block = keyword_fallback(answer, table)
    assert block is not None
    names = [c.name for c in block.concepts]
    assert "梯度下降" in names
    assert "反向传播" in names
    assert all(c.confidence == 0.3 for c in block.concepts)


def test_keyword_fallback_alias_hit():
    table = [{"canonical_name": "卷积", "aliases": ["conv"]}]
    block = keyword_fallback("使用 conv 提取特征", table)
    assert block is not None
    assert block.concepts[0].name == "卷积"


def test_keyword_fallback_no_hit_returns_none():
    table = [{"canonical_name": "梯度下降", "aliases": ["GD"]}]
    assert keyword_fallback("完全不相关的内容", table) is None


def test_keyword_fallback_dedup():
    table = [{"canonical_name": "梯度下降", "aliases": ["GD", "梯度下降"]}]
    block = keyword_fallback("梯度下降 GD", table)
    assert block is not None
    assert len(block.concepts) == 1
