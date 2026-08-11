"""QAStep 评测埋点 NDJSON 发射器（技术架构文档 3.3 / 9.1 + 评测管线数据契约 §1）。

每次概念抽取调用记录 11 字段埋点，只追加写入 NDJSON，供评测管线按 qa_id 回放比对 golden set：
  qa_id, session_id, model, prompt_hash, raw_output, answer_text,
  parsed_concepts, confidence, depth, parent_qa_id, parent_concept_chain (+timestamp)

归一化决策 audit log 走主仓 audit_log 表同通道（评测可按 qa_id 回放全部决策）。
"""
from __future__ import annotations

import json
import logging
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

log = logging.getLogger(__name__)

TELEMETRY_DIR = os.getenv("TELEMETRY_DIR", "telemetry/qa_steps")


def _telemetry_path(qa_id: str) -> Path:
    date = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    p = Path(TELEMETRY_DIR) / date
    p.mkdir(parents=True, exist_ok=True)
    return p / f"{qa_id}.jsonl"


def emit_telemetry(*, qa_id: str, session_id: str, model: str, prompt_hash: str,
                   raw_output: str, answer_text: str, parsed_concepts: list,
                   confidence: float, depth: int = 1,
                   parent_qa_id: Optional[str] = None,
                   parent_concept_chain: Optional[list[str]] = None) -> None:
    """追加一条 11 字段埋点到 NDJSON。失败只记日志，不阻塞主链路。"""
    record = {
        "qa_id": qa_id,
        "session_id": session_id,
        "model": model or "unknown",
        "prompt_hash": prompt_hash,
        "raw_output": raw_output,
        "answer_text": answer_text,
        "parsed_concepts": parsed_concepts,
        "confidence": confidence,
        "depth": depth,
        "parent_qa_id": parent_qa_id,
        "parent_concept_chain": parent_concept_chain or [],
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }
    try:
        with _telemetry_path(qa_id).open("a", encoding="utf-8") as f:
            f.write(json.dumps(record, ensure_ascii=False) + "\n")
    except Exception as e:  # noqa: BLE001
        log.warning("telemetry emit failed qa_id=%s: %s", qa_id, e)
