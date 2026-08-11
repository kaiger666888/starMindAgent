"""约束解码 + 正则降级（协议设计文档四、4.1-4.3）。

主路径：outlines / xgrammar 约束解码（需推理引擎支持，作用于纯 JSON 抽取调用）。
降级路径：正则提取 JSON 块 → Pydantic 校验 → 失败单次重试（仅结构化部分）。

约束解码的「sentinel 触发分段约束」需要引擎运行时激活/去激活 grammar，
仅在自托管 vLLM/Optimum + outlines/xgrammar 场景可用；对标准 OpenAI 兼容 API，
本模块退化为「正则提取 + Pydantic 校验」并可选启用 guided_json 约束纯 JSON 调用。
"""
from __future__ import annotations

import json
import logging
import re
from typing import Optional

from app.schemas import ConceptBlock, ConceptItem

log = logging.getLogger(__name__)

# ConceptBlock 的 JSON Schema（供 guided_json / 约束解码使用）
CONCEPT_BLOCK_SCHEMA: dict = ConceptBlock.model_json_schema()


class ConstrainedDecoder:
    """结构化抽取稳定性兜底：正则提取 + Pydantic 校验。

    `guided_supported` 标识底层后端是否支持 guided JSON（vLLM guided_json /
    OpenAI json_schema response_format）。支持时，纯 JSON 抽取调用会带上 schema
    约束，从生成层消除格式错误；不支持时全程走正则 + Pydantic 降级。
    """

    def __init__(self, guided_supported: bool = False):
        self.guided_supported = guided_supported
        # 检测 outlines / xgrammar 是否安装（仅用于本地引擎场景的标记）
        self._native_constrained = _probe_native_constraint()

    @property
    def constrained_available(self) -> bool:
        """是否具备约束解码能力（guided API 或本地 outlines/xgrammar）。"""
        return self.guided_supported or self._native_constrained

    def extract(self, text: str) -> Optional[ConceptBlock]:
        """从 sentinel 之后的文本中提取并校验 ConceptBlock。

        三步串行，任一步成功即返回：
        1. 直接 json.loads（约束解码路径，JSON 由 schema 保证完整）；
        2. 最外层花括号配对定位 JSON 边界（兼容模型在 JSON 外加围栏 / 说明文字）；
        3. 仍失败返回 None，交由上层走重试 / 降级。
        """
        if text is None:
            return None
        text = text.strip()
        if not text:
            return None

        # 1. 直接解析
        block = self._try_parse(text)
        if block is not None:
            return block

        # 2. 去除 markdown 围栏后再试
        fenced = _strip_code_fence(text)
        if fenced != text:
            block = self._try_parse(fenced)
            if block is not None:
                return block

        # 3. 最外层花括号配对提取
        snippet = _extract_outermost_json(text)
        if snippet is not None:
            block = self._try_parse(snippet)
            if block is not None:
                return block

        return None

    def guided_params(self) -> Optional[dict]:
        """供纯 JSON 抽取调用带上的约束参数（None 表示不约束）。"""
        if not self.constrained_available:
            return None
        # 优先 guided_json（vLLM），回退 response_format（OpenAI 兼容）
        return {
            "guided_json": CONCEPT_BLOCK_SCHEMA,
            "response_format": {
                "type": "json_schema",
                "json_schema": {
                    "name": "ConceptBlock",
                    "schema": CONCEPT_BLOCK_SCHEMA,
                    "strict": False,
                },
            },
        }

    # ----- internals -----
    def _try_parse(self, text: str) -> Optional[ConceptBlock]:
        try:
            data = json.loads(text)
        except (json.JSONDecodeError, ValueError):
            return None
        return self._validate(data)

    def _validate(self, data: object) -> Optional[ConceptBlock]:
        try:
            block = ConceptBlock.model_validate(data)
        except Exception as e:  # ValidationError 或字段缺失
            log.debug("ConceptBlock validation failed: %s", e)
            return None
        if not block.concepts:
            return None
        return block


# ---------------------------------------------------------------------------
# 纯函数工具
# ---------------------------------------------------------------------------
_CODE_FENCE_RE = re.compile(r"^```(?:json)?\s*\n?|\n?\s*```$", re.MULTILINE)


def _strip_code_fence(text: str) -> str:
    return _CODE_FENCE_RE.sub("", text).strip()


def _extract_outermost_json(text: str) -> Optional[str]:
    """用最外层花括号配对定位 JSON 边界（协议 3.3 正则降级路径）。"""
    start = text.find("{")
    if start == -1:
        return None
    depth = 0
    in_str = False
    escape = False
    for i in range(start, len(text)):
        ch = text[i]
        if in_str:
            if escape:
                escape = False
            elif ch == "\\":
                escape = True
            elif ch == '"':
                in_str = False
            continue
        if ch == '"':
            in_str = True
        elif ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                return text[start: i + 1]
    return None  # 未闭合


def _probe_native_constraint() -> bool:
    """探测 outlines / xgrammar 是否安装（仅本地引擎场景）。"""
    for mod in ("outlines", "xgrammar"):
        try:
            __import__(mod)  # noqa: F401
            return True
        except ImportError:
            continue
    return False


def keyword_fallback(
    answer_text: str, concept_table: list[dict]
) -> Optional[ConceptBlock]:
    """L3 关键词匹配兜底（协议 5.2 L3）。

    concept_table 形如 [{"canonical_name": "...", "aliases": [...]}]，
    对 answer_text 做精确子串匹配，命中即产出低置信度 ConceptBlock。
    与概念归一化的别名精确匹配共用同一张别名表，保证降级匹配出的概念也能走归一化合并。
    """
    if not answer_text or not concept_table:
        return None
    hits: list[ConceptItem] = []
    seen = set()
    for entry in concept_table:
        name = entry.get("canonical_name", "")
        candidates = [name] + list(entry.get("aliases", []) or [])
        if any(c and c in answer_text for c in candidates):
            if name in seen:
                continue
            seen.add(name)
            hits.append(
                ConceptItem(
                    name=name,
                    aliases=list(entry.get("aliases", []) or []),
                    confidence=0.3,
                    relation_type="keyword_match",
                )
            )
    if not hits:
        return None
    return ConceptBlock(concepts=hits, model="keyword-fallback")
