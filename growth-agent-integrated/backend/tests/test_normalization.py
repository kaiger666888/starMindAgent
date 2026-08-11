"""归一化三级流水线逻辑测试（桩 embedder/judge，不依赖 DB embedding 服务）。"""
import pytest
from app.concept.thresholds import Thresholds
from app.concept.service import _color_tier


def test_thresholds_defaults():
    t = Thresholds()
    assert t.embedding_high >= t.embedding_low  # 灰区合法
    assert t.embedding_recall_topk >= 1
    # 门禁目标
    assert t.target_merge_precision >= 0.95
    assert t.target_merge_recall >= 0.85


def test_color_tier_mapping():
    """技术架构文档 4.4 热度色彩映射规则。"""
    assert _color_tier(0) == "gray"
    assert _color_tier(1) == "green"
    # >=2 按 1/2/4/8 四档对数映射 淡红->深红
    assert _color_tier(2) == "red_2"
    assert _color_tier(3) == "red_2"
    assert _color_tier(4) == "red_3"
    assert _color_tier(7) == "red_3"
    assert _color_tier(8) == "red_4"
    assert _color_tier(100) == "red_4"
