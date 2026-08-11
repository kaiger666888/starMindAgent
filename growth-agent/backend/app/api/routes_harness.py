"""Harness 可观测路由：GET /harness/obs/metrics。

对应 Harness 设计规格 §五，暴露四项非功能指标 + 端点维度熔断快照，门禁值内联返回。
AI 评测工程师可直接对接（非功能指标门禁评测）。
"""
from __future__ import annotations

from fastapi import APIRouter

from app.harness.app import get_harness

router = APIRouter(prefix="/harness", tags=["harness"])


@router.get("/obs/metrics")
async def obs_metrics():
    """聚合非功能指标快照：中断恢复 / 熔断 / 异步补标注 / 回填延迟。"""
    return get_harness().metrics()


@router.get("/obs/recovery")
async def obs_recovery():
    """中断恢复成功率（架构文档 9.4 门禁 > 95%）。"""
    return {"interruption_recovery": get_harness().metrics()["interruption_recovery"]}


@router.get("/obs/circuit-breaker")
async def obs_circuit_breaker():
    """端点维度熔断快照（告警阈值 > 10%/h）。"""
    return {"circuit_breaker": get_harness().metrics()["circuit_breaker"]}


@router.get("/obs/async-reannotation")
async def obs_async_reannotation():
    """异步补标注完成率（>90%）+ dead letter 比例（<2%）。"""
    return {"async_reannotation": get_harness().metrics()["async_reannotation"]}
