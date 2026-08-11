#!/usr/bin/env python3
"""埋点 NDJSON -> 评测 replay 入口桥接。

把 QAStep 评测埋点（app/qastep/telemetry.py 产出的 NDJSON）聚合成评测管线
`run_eval.py --mode replay` 期望的 JSON 列表，供 AI 评测工程师按 qa_id 回放比对 golden set。

用法：
  python scripts/export_telemetry.py --telemetry-dir telemetry/qa_steps --out telemetry.json
  python eval/scripts/run_eval.py --mode replay --golden-set eval/golden_set/ --telemetry telemetry.json
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path


def collect(telemetry_dir: Path) -> list[dict]:
    records = []
    for f in sorted(telemetry_dir.rglob("*.jsonl")):
        for line in f.read_text("utf-8").splitlines():
            line = line.strip()
            if not line:
                continue
            rec = json.loads(line)
            # 对齐评测管线 QAStepTelemetry 字段
            records.append({
                "qa_id": rec["qa_id"],
                "session_id": rec.get("session_id", ""),
                "model": rec.get("model", "unknown"),
                "prompt_hash": rec.get("prompt_hash", ""),
                "raw_output": rec.get("raw_output", ""),
                "answer_text": rec.get("answer_text", ""),
                "parsed_concepts": rec.get("parsed_concepts", []),
                "confidence": rec.get("confidence", 0.0),
                "depth": rec.get("depth", 1),
            })
    return records


def main():
    ap = argparse.ArgumentParser(description="导出 QAStep 埋点 NDJSON 为评测 replay JSON")
    ap.add_argument("--telemetry-dir", default="telemetry/qa_steps",
                    help="QAStep 埋点 NDJSON 目录")
    ap.add_argument("--out", default="telemetry.json", help="输出 JSON 路径")
    args = ap.parse_args()
    tdir = Path(args.telemetry_dir)
    if not tdir.exists():
        print(f"telemetry dir not found: {tdir}")
        return
    records = collect(tdir)
    Path(args.out).write_text(json.dumps(records, ensure_ascii=False, indent=2), "utf-8")
    print(f"导出 {len(records)} 条埋点 -> {args.out}")
    print(f"回放: python eval/scripts/run_eval.py --mode replay "
          f"--golden-set eval/golden_set/ --telemetry {args.out}")


if __name__ == "__main__":
    main()
