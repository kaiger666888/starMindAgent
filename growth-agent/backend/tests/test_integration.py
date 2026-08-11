"""端到端集成测试：QAStepPipeline 接 harness InferenceSession，L0 主路径 + L1 降级 + 埋点。"""
import asyncio
import json
import os
from pathlib import Path

import pytest

from app.qastep.state_machine import QAStepPipeline, QAStatus
from app.qastep import QAStatus as _QS  # noqa: F401  (确保包导出)


class FakeRepo:
    """不依赖 DB 的 repository 桩，记录状态迁移与埋点。"""
    def __init__(self):
        self.status = QAStatus.GENERATING
        self.answer = ""
        self.telemetry = None

    async def transition(self, qa_id, nxt):
        self.status = nxt

    async def append_answer(self, qa_id, delta):
        self.answer += delta

    async def is_bloat_limit_reached(self, session_id):
        return False

    async def link_co_occurrence(self, qa_id, session_id, concepts):
        pass

    async def persist_telemetry(self, qa_id, *, model, prompt_hash,
                                raw_output, parsed_concepts, aliases, confidence):
        self.telemetry = {"model": model, "prompt_hash": prompt_hash,
                          "raw_output": raw_output, "parsed_concepts": parsed_concepts,
                          "aliases": aliases, "confidence": confidence}


class FakeNormalizer:
    async def normalize(self, item, qa_id, session_id):
        return {"concept_id": f"cid-{item.name}", "canonical_name": item.name,
                "aliases": list(item.aliases), "confidence": item.confidence,
                "relation_type": item.relation_type}

    async def match_existing_only(self, name, session_id):
        return None


async def _run(pipe):
    return [ev async for ev in pipe.run()]


@pytest.mark.asyncio
async def test_e2e_l0_with_harness_session(tmp_path, monkeypatch):
    """L0 主路径：harness InferenceSession + stub backend -> concepts + done + 埋点 NDJSON。"""
    monkeypatch.setattr("app.qastep.telemetry.TELEMETRY_DIR", str(tmp_path))
    from app.harness.app import build_harness
    h = build_harness()  # stub backend
    sess = h.session_for("qa-l0", "s1", "什么是梯度下降？")
    repo = FakeRepo()
    pipe = QAStepPipeline("qa-l0", "s1", "什么是梯度下降？", sess, FakeNormalizer(), repo)
    evs = await _run(pipe)
    types = [e["type"] for e in evs]
    # generating -> delta* -> extracting -> concepts -> waiting -> done
    assert types[0] == "status" and evs[0]["status"] == "generating"
    assert "answer_delta" in types
    assert "concepts" in types and evs[types.index("concepts")]["concepts"]
    assert types[-2] == "status" and evs[-2]["status"] == "waiting"
    assert types[-1] == "done"
    assert repo.status == QAStatus.WAITING
    # 埋点 NDJSON 落盘
    files = list(Path(tmp_path).rglob("*.jsonl"))
    assert files, "telemetry NDJSON 未生成"
    rec = json.loads(files[0].read_text("utf-8").strip())
    assert rec["qa_id"] == "qa-l0"
    assert rec["model"] and rec["prompt_hash"]
    assert isinstance(rec["parsed_concepts"], list) and rec["parsed_concepts"]
    assert rec["parsed_concepts"][0]["canonical_name"] == "概念A"


@pytest.mark.asyncio
async def test_e2e_l1_degradation_keeps_prose(tmp_path, monkeypatch):
    """L1 降级：正文流出后 JSON 失败 -> 丢弃结构化只返回正文，仍进 waiting。"""
    monkeypatch.setattr("app.qastep.telemetry.TELEMETRY_DIR", str(tmp_path))
    from app.inference.protocol import FailingInferenceSession
    repo = FakeRepo()
    pipe = QAStepPipeline("qa-l1", "s1", "问题", FailingInferenceSession("qa-l1", "s1", "问题"),
                          FakeNormalizer(), repo)
    evs = await _run(pipe)
    types = [e["type"] for e in evs]
    assert "answer_delta" in types       # 正文已渲染
    assert "error" in types             # L1 降级
    assert "concepts" not in types       # 无概念标注
    assert repo.status == QAStatus.WAITING  # 仍进 waiting（不阻塞主流程）


@pytest.mark.asyncio
async def test_harness_metrics_route_shape():
    """/harness/obs/metrics 返回四项指标结构。"""
    from app.harness.app import get_harness
    m = get_harness().metrics()
    assert {"interruption_recovery", "circuit_breaker",
            "async_reannotation", "backfill_latency"} <= set(m)
