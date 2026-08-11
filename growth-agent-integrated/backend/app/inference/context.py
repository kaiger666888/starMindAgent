"""上下文膨胀控制（技术架构文档第十一节 / 协议设计文档）。

深层 prompt token 随下钻深度急剧增长，需压缩：只保留概念链摘要，
丢弃非当前分支的兄弟节点，整体 ≤ 2K token。

预算分配（参考上游推理框架规格）：
  system prompt   ~400 token
  概念链摘要      ~1000 token
  当前问题        ~400 token
  余量            ~200 token
"""
from __future__ import annotations

import logging
from dataclasses import dataclass

log = logging.getLogger(__name__)

CONTEXT_TOKEN_BUDGET = 2000
_SYSTEM_BUDGET = 400
_CHAIN_BUDGET = 1000
_QUESTION_BUDGET = 400
_RESERVE = 200

SYSTEM_PROMPT = (
    "你是「伴你成长」学习助手。先输出自然语言回答，"
    "然后另起一行输出 sentinel 行，再输出 ConceptBlock JSON。"
    "禁止在正文中出现 sentinel 标记。"
)


@dataclass
class ChainNode:
    depth: int
    question: str
    concept: str          # 该层下钻的概念 canonical_name
    siblings: list[str]   # 同层兄弟概念（已丢弃，仅留计数用于摘要）


def _approx_tokens(text: str) -> int:
    """粗估 token 数：中文按字、英文按 4 字符/token。"""
    cjk = sum(1 for c in text if "\u4e00" <= c <= "\u9fff")
    other = len(text) - cjk
    return cjk + max(other // 4, 0)


def compress_chain(chain: list[ChainNode]) -> str:
    """把概念链压缩成 ≤ _CHAIN_BUDGET token 的摘要。

    策略：
    1. 逐层压缩为「第N层：问题 → 概念」单行；
    2. 优先保留靠近当前层的节点（深层更相关），超限时丢弃最浅层细节只留概念名；
    3. 兄弟节点（非当前分支）直接丢弃，仅当前分支入摘要。
    """
    if not chain:
        return ""
    lines: list[str] = []
    # 从浅到深排
    ordered = sorted(chain, key=lambda n: n.depth)
    for n in ordered:
        lines.append(f"L{n.depth}: {n.concept}（{n.question[:20]}）")
    summary = "\n".join(lines)
    while _approx_tokens(summary) > _CHAIN_BUDGET and len(lines) > 1:
        # 丢弃最浅层细节，只留概念名
        first = ordered.pop(0)
        lines = [f"L{n.depth}: {n.concept}" for n in ordered]
        summary = "\n".join(lines)
        if not ordered:
            break
    return summary


def build_prompt(question: str, chain: list[ChainNode] | None = None) -> str:
    """组装受膨胀控制的 prompt，总 token ≤ CONTEXT_TOKEN_BUDGET。"""
    chain_summary = compress_chain(chain or [])
    parts = [SYSTEM_PROMPT[:_SYSTEM_BUDGET], chain_summary, question[:_QUESTION_BUDGET]]
    blob = "\n\n".join(p for p in parts if p)
    # 兜底：超预算则按比例截断概念链摘要
    while _approx_tokens(blob) > CONTEXT_TOKEN_BUDGET - _RESERVE and chain_summary:
        chain_summary = chain_summary[: int(len(chain_summary) * 0.7)]
        parts = [SYSTEM_PROMPT[:_SYSTEM_BUDGET], chain_summary, question[:_QUESTION_BUDGET]]
        blob = "\n\n".join(p for p in parts if p)
        if len(chain_summary) < 8:
            break
    return blob
