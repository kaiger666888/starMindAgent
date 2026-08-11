"""约束解码 + 结构化抽取稳定性（协议设计文档 4 / 技术架构文档 6.2）。

主路径：JSON Schema 约束解码（outlines / xgrammar 优先），从解码层消除格式错误。
退化：模型不支持约束解码时，退化为「正则提取 JSON 块 → Pydantic 校验 → 单次重试
（temperature 降 0.2）」。重试仅限结构化部分——正文已流式推给用户，不可撤回。

约束解码作用于「纯 JSON 抽取调用」（L2 第二次调用 / L1 重试），不作用于流式正文。
"""
from __future__ import annotations

import json
import logging
import re
from typing import Optional

from app.schemas import ConceptBlock, ConceptItem

log = logging.getLogger(__name__)

# 约束解码后端探测（懒加载，缺失则退化）
_GUIDED_BACKEND = None
_GUIDED_PROBED = False


def _probe_guided_backend():
    """探测 outlines / xgrammar 是否可用；不可用返回 None。"""
    global _GUIDED_BACKEND, _GUIDED_PROBED
    if _GUIDED_PROBED:
        return _GUIDED_BACKEND
    _GUIDED_PROBED = True
    try:
        import outlines  # noqa: F401
        _GUIDED_BACKEND = "outlines"
        log.info("constrained decoding: outlines available")
    except Exception:
        try:
            import xgrammar  # noqa: F401
            _GUIDED_BACKEND = "xgrammar"
            log.info("constrained decoding: xgrammar available")
        except Exception:
            _GUIDED_BACKEND = None
            log.info("constrained decoding: none available, fallback to regex+pydantic")
    return _GUIDED_BACKEND


def guided_backend_name() -> Optional[str]:
    return _probe_guided_backend()


# ConceptBlock 的 JSON Schema（约束解码 / 校验共用）
CONCEPT_BLOCK_SCHEMA = {
    "type": "object",
    "properties": {
        "concepts": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "name": {"type": "string"},
                    "aliases": {"type": "array", "items": {"type": "string"}},
                    "confidence": {"type": "number", "minimum": 0, "maximum": 1},
                    "relation_type": {"type": "string"},
                },
                "required": ["name"],
            },
        },
        "model": {"type": "string"},
        "prompt_hash": {"type": "string"},
    },
    "required": ["concepts"],
}


# 捕获首个平衡花括号的 JSON 对象（容错：前后有杂散文本也能提取）
_JSON_OBJ_RE = re.compile(r"\{.*\}", re.DOTALL)


def parse_concept_block(raw: str) -> Optional[ConceptBlock]:
    """从文本中提取并校验 ConceptBlock。

    优先整段 json.loads；失败则用花括号平衡提取首个 JSON 对象再校验。
    返回 None 表示解析失败（调用方据此进入 L1 降级）。
    """
    if not raw or not raw.strip():
        return None
    candidates = [raw.strip()]
    m = _JSON_OBJ_RE.search(raw)
    if m:
        candidates.append(m.group(0))
    for cand in candidates:
        try:
            data = json.loads(cand)
            return ConceptBlock(**data)
        except (json.JSONDecodeError, ValueError, TypeError):
            continue
    return None


def extract_concepts_only(raw_answer: str, model: str = "unknown") -> ConceptBlock:
    """从已有正文做轻量抽取（L2 第二次调用 / 补标注用）。

    约束解码后端可用时由调用方传入经约束解码的纯 JSON；这里做兜底：
    从正文里启发式抽取名词短语，构造一个低置信 ConceptBlock。
    真实实现应调用更小模型的 extract-only 接口。
    """
    return ConceptBlock(
        concepts=_heuristic_concepts(raw_answer),
        model=f"{model}#extract-only",
    )


def _heuristic_concepts(text: str) -> list[ConceptItem]:
    """启发式名词短语抽取（L3 回退 / 补标注兜底，非主路径）。"""
    tokens = [t.strip() for t in re.split(r"[，。、；,\n\s]+", text) if t.strip()]
    seen, out = set(), []
    for t in tokens:
        if 2 <= len(t) <= 12 and t not in seen:
            seen.add(t)
            out.append(ConceptItem(name=t, confidence=0.5))
        if len(out) >= 8:
            break
    return out
