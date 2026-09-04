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
_MATERIAL_BUDGET = 600  # 学习材料相关段落，独立槽，不挤占 question
_SELECTION_BUDGET = 400  # 就选段提问：用户在当前页面选中的原文引用
_RESERVE = 200

SYSTEM_PROMPT = (
    "你是「伴你成长」学习助手。先输出自然语言回答，"
    "然后另起一行输出 sentinel 行，再输出 ConceptBlock JSON。"
    "禁止在正文中出现 sentinel 标记。"
    # 长度纪律：探索树每层短小精悍。不限长度时实测单层可达 7900+ 字，
    # 长输出挤掉 sentinel JSON（概念抽取退兜底，3-4 倍调用放大），
    # 且长 prompt/长输出直接推高推理侧内存峰值（网关 1210 OOM）。
    "回答精炼：正文控制在 500 字以内，聚焦当前问题的核心。"
    # 防同质化：下钻层的核心诉求是"从目标概念展开"，而非概括上层主题。
    # 不约束时模型倾向写主题概述（最安全的答法），导致每层回答趋同。
    "回答必须围绕问题中的目标概念本身展开（它是什么、机制/原理、典型示例），"
    "禁止写成对上层主题的概括性复读；"
    "若提供了探索路径，聚焦该概念在父概念语境中的具体角色，"
    "不重复上层已解释过的内容。"
)


@dataclass
class ChainNode:
    depth: int
    question: str
    concept: str          # 该层下钻的概念 canonical_name
    siblings: list[str]   # 同层兄弟概念（已丢弃，仅留计数用于摘要）
    summary: str = ""     # 该层后台生成的总结（滑动窗口传导：深层完整保留，超预算从最浅层丢）


def _approx_tokens(text: str) -> int:
    """粗估 token 数：中文按字、英文按 4 字符/token。"""
    cjk = sum(1 for c in text if "\u4e00" <= c <= "\u9fff")
    other = len(text) - cjk
    return cjk + max(other // 4, 0)


def _chain_line(n: ChainNode, with_summary: bool) -> str:
    """单层摘要行。with_summary=False 时只留概念名（被窗口挤出的浅层）。"""
    if not with_summary or not n.summary:
        return f"L{n.depth}: {n.concept}（{n.question[:20]}）"
    return f"L{n.depth}: {n.concept} — {n.summary.strip()}"


def compress_chain(chain: list[ChainNode]) -> str:
    """把概念链压成 ≤ _CHAIN_BUDGET token 的摘要（滑动窗口语义）。

    每层的总结沿链向深层传导：越靠近当前层的越完整保留，
    超预算时从最浅层开始丢总结只留概念名（窗口向深侧滑动），
    保证"针对全局"的语境永远优先覆盖最近的探索脉络。
    """
    if not chain:
        return ""
    # 从浅到深排
    ordered = sorted(chain, key=lambda n: n.depth)
    header = "用户探索路径（浅 -> 深，仅作背景定位，不要复述）："
    have_summary = [bool(n.summary) for n in ordered]

    def _join(with_idx: set[int]) -> str:
        lines = [_chain_line(n, i in with_idx) for i, n in enumerate(ordered)]
        return header + "\n" + "\n".join(lines)

    # 全量（含所有已有总结）
    summary = _join({i for i, s in enumerate(have_summary) if s})
    # 超预算：从最浅层开始丢总结（窗口向深侧滑动）
    for i in range(len(ordered)):
        if _approx_tokens(summary) <= _CHAIN_BUDGET:
            break
        if not have_summary[i]:
            continue
        have_summary[i] = False
        summary = _join({j for j, s in enumerate(have_summary) if s})
    return summary


def build_prompt(question: str, chain: list[ChainNode] | None = None,
                 material_context: str | None = None,
                 selection: str | None = None) -> str:
    """组装受膨胀控制的 prompt，总 token ≤ CONTEXT_TOKEN_BUDGET。

    material_context：学习材料相关段落（导入文件时按 question 检索出的相关 chunk），
    单独预算槽（_MATERIAL_BUDGET），不拼进 question，避免污染存库的 question。

    selection：就选段提问时用户在当前页面选中的原文。用户的短问题（如"为什么"）
    必须结合选中原文 + 探索路径总结理解，而不是孤立回答三个字。
    """
    chain_summary = compress_chain(chain or [])
    parts = [
        SYSTEM_PROMPT[:_SYSTEM_BUDGET],
        chain_summary,
    ]
    if material_context:
        # 截到材料预算内
        parts.append(material_context[:_MATERIAL_BUDGET])
    if selection:
        parts.append(
            "用户在当前页面选中的原文（用户的提问针对这段，结合它作答）：\n"
            + selection[:_SELECTION_BUDGET]
        )
    parts.append(question[:_QUESTION_BUDGET])
    blob = "\n\n".join(p for p in parts if p)
    # 兜底：超预算则按比例截断概念链摘要
    while _approx_tokens(blob) > CONTEXT_TOKEN_BUDGET - _RESERVE and chain_summary:
        chain_summary = chain_summary[: int(len(chain_summary) * 0.7)]
        parts = [SYSTEM_PROMPT[:_SYSTEM_BUDGET], chain_summary]
        if material_context:
            parts.append(material_context[:_MATERIAL_BUDGET])
        if selection:
            parts.append(
                "用户在当前页面选中的原文（用户的提问针对这段，结合它作答）：\n"
                + selection[:_SELECTION_BUDGET]
            )
        parts.append(question[:_QUESTION_BUDGET])
        blob = "\n\n".join(p for p in parts if p)
        if len(chain_summary) < 8:
            break
    return blob
