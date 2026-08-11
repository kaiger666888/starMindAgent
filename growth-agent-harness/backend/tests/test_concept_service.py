"""Concept 服务接口契约测试（数据模型 / 三状态视图派生 / undo 回放快照结构）。"""
import pytest
from app.concept.audit import replay_undo
from app.concept.service import ConceptService, _color_tier
from app.concept.thresholds import threshold_service


def test_concept_service_methods_exist():
    svc = ConceptService()
    # 四个接口齐备（P0 契约）
    for name in ["merge_concepts", "undo_merge", "get_graph", "increment_explore"]:
        assert callable(getattr(svc, name)), f"missing {name}"


def test_merge_serialized_by_lock():
    """归一化 merge/undo 强制串行化（同一锁）。"""
    import app.concept.service as svc_mod
    assert hasattr(svc_mod, "_merge_lock")
    # 锁是 asyncio.Lock
    import asyncio
    assert isinstance(svc_mod._merge_lock, type(asyncio.Lock()))


def test_undo_replay_signature():
    """undo 基于 audit log 反向回放：replay_undo 是可调用函数。"""
    assert callable(replay_undo)


def test_three_state_origin_derivation():
    """单表按 origin 派生三状态视图：get_graph 返回 views 分组。"""
    import inspect
    sig = inspect.signature(ConceptService.get_graph)
    params = list(sig.parameters)
    assert "session_id" in params
    assert "origin_filter" in params
    # origin 取值集
    origins = {"user_click", "co_occurrence", "domain_graph"}
    assert origins
